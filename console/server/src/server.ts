import express from "express";
import fs from "fs";
import { existsSync } from "node:fs";
import path from "path";
import { fileURLToPath } from "url";
import { createPool } from "./db/client.js";
import { runMigrations, seedGroupsFromYaml } from "./db/migrate.js";
import { loadAgents, enrichAgentsWithDbGroups } from "./agent-registry.js";
import type { Agent } from "./agent-registry.js";
import { loadGroups, resolveUserGroupsWithDb, listCustomGroups } from "./groups.js";
import type { Group } from "./groups.js";
import { makeAuthRouter } from "./routes/auth.js";
import { makeAgentsRouter } from "./routes/agents.js";
import { makeSessionsRouter } from "./routes/sessions.js";
import { makeTasksRouter } from "./routes/tasks.js";
import { makeMessagesRouter } from "./routes/messages.js";
import { makeProxyRouter } from "./routes/proxy.js";
import { groupsRouter } from "./routes/groups.js";
import { makeAdminRouter } from "./routes/admin.js";
import { SessionStore } from "./session-store.js";
import { TaskStore } from "./task-store.js";
import { GatewayClient } from "./gateway-client.js";
import { startReconciler } from "./task-reconciler.js";
import { requireAuth } from "./middleware/require-auth.js";
import { attachAuthContext } from "./middleware/attach-auth.js";
import { ScheduleStore } from "./schedule-store.js";
import { makeSchedulesRouter } from "./routes/schedules.js";
import { startScheduler } from "./scheduler.js";
import { UsageStore } from "./usage-store.js";
import { PricingStore } from "./pricing.js";
import { usageRouter } from "./routes/usage.js";
import { reportRouter } from "./routes/reports.js";
import { makeAgentMetricsRouter } from "./routes/agent-metrics.js";
import { PermissionStore } from "./permission-store.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = parseInt(process.env.PORT ?? "3001", 10);
const SESSION_SECRET = process.env.SESSION_SECRET;
const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const AGENTS_CONFIG_PATH = process.env.AGENTS_CONFIG_PATH ?? "./config/agents.yaml";
const GROUPS_CONFIG_PATH = process.env.GROUPS_CONFIG_PATH ?? "./config/groups.yaml";
const RECONCILER_INTERVAL_MS = parseInt(process.env.RECONCILER_INTERVAL_MS ?? "60000", 10);
const SCHEDULER_INTERVAL_MS = parseInt(process.env.SCHEDULER_INTERVAL_MS ?? "30000", 10);
const MIGRATIONS_DIR = path.resolve(__dirname, "db/migrations");
const ADMIN_GROUP = process.env.ADMIN_GROUP ?? "admins";

const REPO_ROOT = path.resolve(__dirname, "../../");

if (!SESSION_SECRET) { console.error("SESSION_SECRET env var is required"); process.exit(1); }
if (!DB_URL) { console.error("CHAT_UI_DATABASE_URL env var is required"); process.exit(1); }

async function main() {
  const pool = createPool(DB_URL!);

  try {
    await pool.query("SELECT 1");
    console.log("DB connection OK");
  } catch (err) {
    console.error("Failed to connect to DB:", err);
    process.exit(1);
  }

  // Run migrations
  try {
    await runMigrations(pool, MIGRATIONS_DIR);
    console.log("Migrations OK");
  } catch (err) {
    console.error("Migration failed:", err);
    process.exit(1);
  }

  // Load YAML configs
  let agents: Agent[];
  let groups: Group[];

  try {
    const agentsPath = path.isAbsolute(AGENTS_CONFIG_PATH)
      ? AGENTS_CONFIG_PATH
      : fs.existsSync(path.resolve(process.cwd(), AGENTS_CONFIG_PATH))
        ? path.resolve(process.cwd(), AGENTS_CONFIG_PATH)
        : path.resolve(REPO_ROOT, AGENTS_CONFIG_PATH);
    agents = loadAgents(agentsPath);
    console.log(`Loaded ${agents.length} agent(s) from ${agentsPath}`);
  } catch (err) {
    console.error("Failed to load agents config:", err);
    process.exit(1);
  }

  try {
    const groupsPath = path.isAbsolute(GROUPS_CONFIG_PATH)
      ? GROUPS_CONFIG_PATH
      : fs.existsSync(path.resolve(process.cwd(), GROUPS_CONFIG_PATH))
        ? path.resolve(process.cwd(), GROUPS_CONFIG_PATH)
        : path.resolve(REPO_ROOT, GROUPS_CONFIG_PATH);
    groups = loadGroups(groupsPath);
    console.log(`Loaded ${groups.length} group(s) from ${groupsPath}`);
  } catch (err) {
    console.error("Failed to load groups config:", err);
    process.exit(1);
  }

  // Instantiate stores
  const sessionStore = new SessionStore(pool);
  const taskStore = new TaskStore(pool);
  const scheduleStore = new ScheduleStore(pool);
  const usageStore = new UsageStore(pool, new PricingStore(pool));
  const permStore = new PermissionStore(pool);

  // Seed groups from YAML if custom_groups table is empty
  try {
    await seedGroupsFromYaml(groups, agents, pool);
    console.log("Group seeding OK");
  } catch (err) {
    console.error("Group seeding failed:", err);
    // Non-fatal — continue without seeding
  }

  const makeClient = (gatewayUrl: string) => new GatewayClient(gatewayUrl);

  async function resolveGroups(email: string): Promise<string[]> {
    return resolveUserGroupsWithDb(groups, email, pool);
  }

  async function getAllGroups(): Promise<Group[]> {
    const customGroups = await listCustomGroups(pool);
    const customAsGroup: Group[] = customGroups.map((cg) => ({
      id: cg.id,
      name: cg.name,
      members: [],
    }));
    const yamlIds = new Set(groups.map((g) => g.id));
    return [...groups, ...customAsGroup.filter((cg) => !yamlIds.has(cg.id))];
  }

  const app = express();
  app.use(express.json());

  app.get("/health", (_req, res) => {
    res.json({ ok: true, ts: new Date().toISOString() });
  });

  // Auth routes
  app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET!, {
    resolveGroups,
    adminGroup: ADMIN_GROUP,
    permStore,
    getAgentIds: () => agents.map(a => a.id),
  }));

  // ── All routes below require auth ──────────────────────────────────────

  const auth = requireAuth(SESSION_SECRET!);
  const authz = attachAuthContext(() => groups, resolveGroups, ADMIN_GROUP, permStore);

  app.use("/api/agents", auth, authz, makeAgentsRouter({ pool, getAgents: () => agents }));

  app.use("/api", auth, authz,
    makeSessionsRouter({
      store: sessionStore, taskStore,
      getAgents: () => agents,
      getEnrichedAgents: () => enrichAgentsWithDbGroups(agents, pool),
      makeClient,
    })
  );

  app.use("/api", auth, authz,
    makeTasksRouter({
      taskStore, sessionStore,
      getAgents: () => agents,
      getEnrichedAgents: () => enrichAgentsWithDbGroups(agents, pool),
      makeClient,
    })
  );

  app.use("/api", auth, authz,
    makeMessagesRouter({ pool, taskStore, sessionStore, getAgents: () => agents, makeClient })
  );

  app.use("/api/sessions", auth, authz,
    makeProxyRouter({ taskStore, sessionStore, pool, getAgents: () => agents, makeClient })
  );

  app.use("/api/groups", auth, authz, groupsRouter({ getGroups: () => groups }));

  app.use("/api/admin", auth, authz,
    makeAdminRouter({ pool, getAgents: () => agents, getGroups: () => groups, getAllGroups, getEnrichedAgents: () => enrichAgentsWithDbGroups(agents, pool) })
  );

  app.use("/api/schedules", auth, authz,
    makeSchedulesRouter({ scheduleStore, sessionStore, taskStore, getAgents: () => agents, makeClient })
  );

  const gatewayClients = new Map<string, GatewayClient>();
  for (const agent of agents) {
    gatewayClients.set(agent.id, makeClient(agent.gateway_url));
  }
  usageStore.setGatewayClients(gatewayClients);
  app.use("/api/usage", auth, authz, usageRouter({ usageStore, gatewayClients, pool }));

  app.use("/api/reports", auth, authz, reportRouter);
  app.use("/api/agent-metrics", auth, authz, makeAgentMetricsRouter(pool));

  // Auto-backfill
  const backfillTimer = setInterval(() => {
    usageStore.backfillAllAgents().then((results) => {
      for (const r of results) {
        console.log(`[backfill] ${r.agentId}: ${r.sessions} sessions, ${r.errors} errors`);
      }
    }).catch((err) => { console.error("[backfill] error:", err); });
  }, 60 * 60 * 1000);
  setTimeout(() => { usageStore.backfillAllAgents().catch(() => {}); }, 30_000);

  // Start reconciler
  const stopReconciler = startReconciler(
    { pool, taskStore, sessionStore, getAgents: () => agents, makeClient },
    RECONCILER_INTERVAL_MS
  );

  // Start scheduler
  const stopScheduler = startScheduler(
    { scheduleStore, taskStore, sessionStore, getAgents: () => agents, makeClient },
    SCHEDULER_INTERVAL_MS
  );

  const publicDir = path.resolve("public");
  if (existsSync(publicDir)) {
    app.use(express.static(publicDir));
    app.get(/^(?!\/api|\/health).*$/, (_req, res) => {
      res.sendFile(path.join(publicDir, "index.html"));
    });
  }

  const server = app.listen(PORT, () => {
    console.log(`Server listening on http://127.0.0.1:${PORT}`);
  });

  process.on("SIGTERM", () => {
    clearInterval(backfillTimer);
    stopReconciler();
    stopScheduler();
    server.close(() => process.exit(0));
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
