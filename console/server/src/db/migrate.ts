import fs from "fs";
import path from "path";
import type pg from "pg";
import type { Group } from "../groups.js";
import type { Agent } from "../agent-registry.js";
import { DASHBOARD_IDS } from "../dashboard-registry.js";
import { hashPassword } from "../auth.js";

export interface SeedAccount {
  email: string;
  password: string;
  name: string;
}

/**
 * Create the accounts named in the deployment config on startup.
 *
 * A fresh deployment has an empty users table, so nobody can sign in until an
 * account exists. Accounts that already exist are never modified. An empty
 * email or a password shorter than the signup minimum is skipped.
 */
export async function seedAccounts(pool: pg.Pool, accounts: SeedAccount[]): Promise<void> {
  for (const account of accounts) {
    const email = account.email.toLowerCase().trim();
    if (!email || !account.password) continue;
    if (account.password.length < 8) {
      console.warn(`Skipping account ${email}: password must be at least 8 characters`);
      continue;
    }
    const existing = await pool.query("SELECT 1 FROM users WHERE email = $1", [email]);
    if (existing.rows.length > 0) continue;

    await pool.query(
      `INSERT INTO users (email, password_hash, name) VALUES ($1, $2, $3)
       ON CONFLICT (email) DO NOTHING`,
      [email, await hashPassword(account.password), account.name]
    );
    console.log(`Created account: ${email}`);
  }
}

export async function runMigrations(pool: pg.Pool, migrationsDir: string): Promise<void> {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS _migrations (
      filename TEXT PRIMARY KEY,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `);

  const files = fs.readdirSync(migrationsDir).filter((f) => f.endsWith(".sql") && !f.startsWith(".")).sort();

  for (const file of files) {
    const { rows } = await pool.query(
      "SELECT 1 FROM _migrations WHERE filename = $1",
      [file]
    );
    if (rows.length > 0) continue;

    const sql = fs.readFileSync(path.join(migrationsDir, file), "utf8");
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(sql);
      await client.query("INSERT INTO _migrations(filename) VALUES($1)", [file]);
      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }
}

/**
 * Seed custom_groups, group_memberships, agent_group_access, and
 * group_dashboard_permissions from YAML if not already seeded.
 * Also ensures the `admins` group exists with interactive access to all agents.
 * Run after migrations on server startup.
 */
export async function seedGroupsFromYaml(
  yamlGroups: Group[],
  agents: Agent[],
  pool: pg.Pool,
): Promise<void> {
  // ── Ensure the `admins` group exists with interactive on all agents ──────
  const adminGroupId = "admins";
  const adminGroup = await pool.query("SELECT id FROM custom_groups WHERE id = $1", [adminGroupId]);
  if (adminGroup.rows.length === 0) {
    await pool.query(
      `INSERT INTO custom_groups (id, name, created_by) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING`,
      [adminGroupId, "Admins", null]
    );
    console.log(`Created group: ${adminGroupId}`);
  }

  // Ensure admins group has interactive access to ALL agents
  for (const agent of agents) {
    await pool.query(
      `INSERT INTO agent_group_access (agent_id, group_id, access_level)
       VALUES ($1, $2, 'interactive')
       ON CONFLICT (agent_id, group_id) DO NOTHING`,
      [agent.id, adminGroupId]
    );
  }

  // Ensure admins group has all dashboards enabled
  for (const dashboard of DASHBOARD_IDS) {
    await pool.query(
      `INSERT INTO group_dashboard_permissions (group_id, dashboard, allowed)
       VALUES ($1, $2, true)
       ON CONFLICT (group_id, dashboard) DO NOTHING`,
      [adminGroupId, dashboard]
    );
  }

  // ── Seed YAML groups ────────────────────────────────────────────────────
  for (const group of yamlGroups) {
    // Insert group if not exists
    const existing = await pool.query("SELECT id FROM custom_groups WHERE id = $1", [group.id]);
    if (existing.rows.length === 0) {
      await pool.query(
        `INSERT INTO custom_groups (id, name, created_by) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING`,
        [group.id, group.name, null]
      );
    }

    // Insert members
    for (const email of group.members) {
      // Only for accounts that exist: on a fresh database the YAML members have
      // not signed up yet, and the foreign key rejects them. resolveUserGroups()
      // still grants their access from YAML in the meantime.
      await pool.query(
        `INSERT INTO group_memberships (group_id, user_email)
         SELECT $1, $2 WHERE EXISTS (SELECT 1 FROM users WHERE email = $2)
         ON CONFLICT (group_id, user_email) DO NOTHING`,
        [group.id, email]
      );
    }

    // Set agent permissions from YAML access.groups[] — interactive by default
    for (const agent of agents) {
      const agentGroups = (agent as any).access?.groups ?? [];
      if (agentGroups.includes(group.id)) {
        await pool.query(
          `INSERT INTO agent_group_access (agent_id, group_id, access_level)
           VALUES ($1, $2, 'interactive') ON CONFLICT (agent_id, group_id) DO NOTHING`,
          [agent.id, group.id]
        );
      }
    }

    // Set dashboard permissions
    const isAdminGroup = group.id === "admins";
    const dashboards = [
      { name: "availability", allowed: true },
      { name: "reliability", allowed: isAdminGroup },
      { name: "usage", allowed: isAdminGroup },
    ];
    for (const d of dashboards) {
      await pool.query(
        `INSERT INTO group_dashboard_permissions (group_id, dashboard, allowed)
         VALUES ($1, $2, $3) ON CONFLICT (group_id, dashboard) DO NOTHING`,
        [group.id, d.name, d.allowed]
      );
    }
  }
}
