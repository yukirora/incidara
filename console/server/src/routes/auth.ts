import { Router } from "express";
import { z } from "zod";
import type pg from "pg";
import { signup, login, ensureUser } from "../auth.js";
import { getSession } from "../session-cookie.js";
import { PermissionStore } from "../permission-store.js";
import { DASHBOARD_IDS } from "../dashboard-registry.js";

const SignupSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
  name: z.string().optional(),
});

const LoginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

const GATEWAY_URL = process.env.AUTH_GATEWAY_URL || "http://192.0.2.10:8200";

interface AuthRouterOptions {
  resolveGroups?: (email: string) => Promise<string[]>;
  adminGroup?: string;
  permStore?: PermissionStore;
  getAgentIds?: () => string[];
}

/** Build the user object returned on login/me, including permission data */
async function buildUserResponse(
  email: string,
  isAdmin: boolean,
  groups: string[],
  permStore?: PermissionStore,
  allAgents: string[] = [],
) {
  let agentLevels: Record<string, string> = {};
  let dashboardAccess: Record<string, boolean> = {};

  if (permStore) {
    try {
      agentLevels = await permStore.getUserAgentLevels(email);
      dashboardAccess = await permStore.getUserDashboardAccess(email);
    } catch {
      // fail gracefully
    }
  }

  // Admin users get interactive on ALL agents, even if DB doesn't have entries
  if (isAdmin) {
    for (const agentId of allAgents) {
      if (!(agentId in agentLevels)) {
        agentLevels[agentId] = "interactive";
      }
    }
    // Admins get all dashboards (from registry)
    for (const dash of DASHBOARD_IDS) {
      dashboardAccess[dash] = true;
    }
  }

  return {
    email,
    is_admin: isAdmin,
    groups,
    agent_levels: agentLevels,
    dashboard_access: dashboardAccess,
  };
}

export function makeAuthRouter(
  pool: pg.Pool,
  sessionSecret: string,
  options: AuthRouterOptions = {}
): Router {
  const { resolveGroups, adminGroup, permStore, getAgentIds } = options;
  const router = Router();

  // --- Auth Gateway: redirect to gateway login page ---
  router.get("/gateway/login", (req, res) => {
    const next = req.query.next as string || req.headers.referer || "/";
    const protocol = req.headers["x-forwarded-proto"] || req.protocol;
    const host = req.headers["x-forwarded-host"] || req.get("host");
    const callbackUrl = `${protocol}://${host}/api/auth/gateway/callback`;
    const gatewayLoginUrl = `${GATEWAY_URL}/login?next=${encodeURIComponent(callbackUrl + "?next=" + encodeURIComponent(next))}`;
    res.redirect(gatewayLoginUrl);
  });

  // --- Auth Gateway: callback after successful gateway login ---
  router.get("/gateway/callback", async (req, res): Promise<void> => {
    const gatewayToken = req.query.gateway_token as string;
    const next = (req.query.next as string) || "/";

    if (!gatewayToken) {
      res.redirect(`/login?error=missing_token`);
      return;
    }

    try {
      const verifyResp = await fetch(`${GATEWAY_URL}/verify`, {
        headers: { Authorization: `Bearer ${gatewayToken}` },
        signal: AbortSignal.timeout(5000),
      });

      if (!verifyResp.ok) {
        res.redirect(`/login?error=gateway_failed`);
        return;
      }

      const verifyData = await verifyResp.json() as { valid?: boolean; user?: string; admin?: boolean };
      if (!verifyData.valid || !verifyData.user) {
        res.redirect(`/login?error=invalid_token`);
        return;
      }

      const ltpUser = verifyData.user;
      const isAdmin = verifyData.admin ?? false;

      // Try to match existing user by LTP username
      let email: string;
      const usernamePrefix = ltpUser.includes("@") ? ltpUser : ltpUser + "@";
      const { rows: existingUsers } = await pool.query<{ email: string }>(
        "SELECT email FROM users WHERE email ILIKE $1 ORDER BY created_at ASC LIMIT 1",
        [usernamePrefix + "%"]
      );

      if (existingUsers.length > 0) {
        email = existingUsers[0].email;
      } else {
        email = ltpUser.includes("@") ? ltpUser : `${ltpUser}@ltp`;
      }

      const user = await ensureUser(pool, { email, name: ltpUser, isAdmin });

      // Auto-add gateway admin users to the `admins` group in DB
      // so that permStore.getUserAgentLevels() returns interactive on all agents
      if (isAdmin) {
        try {
          await pool.query(
            `INSERT INTO group_memberships (group_id, user_email)
             VALUES ('admins', $1)
             ON CONFLICT (group_id, user_email) DO NOTHING`,
            [email]
          );
          // Invalidate permission cache so the new membership is picked up
          options.permStore?.invalidate();
        } catch {
          // Non-fatal — admin still has isAdmin=true in session
        }
      }

      const session = await getSession(req, res, sessionSecret);
      session.userEmail = user.email;
      session.userId = user.id;
      session.isAdmin = isAdmin;
      await session.save();

      res.redirect(next);
    } catch (err) {
      console.error("[auth] gateway callback error:", err);
      res.redirect(`/login?error=gateway_failed`);
    }
  });

  // --- Local signup (for admin-created users — no self-signup) ---
  router.post("/signup", async (req, res): Promise<void> => {
    const parsed = SignupSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }
    try {
      const user = await signup(pool, parsed.data);
      const session = await getSession(req, res, sessionSecret);
      session.userEmail = user.email;
      session.userId = user.id;
      await session.save();
      res.status(201).json({ user: { id: user.id, email: user.email, name: user.name } });
    } catch (err: unknown) {
      const e = err as Error;
      if (e.message === "Email already registered") {
        res.status(409).json({ error: e.message });
      } else {
        res.status(500).json({ error: "Internal server error" });
      }
    }
  });

  // --- Local login ---
  router.post("/login", async (req, res): Promise<void> => {
    const parsed = LoginSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }
    try {
      const user = await login(pool, parsed.data);
      const session = await getSession(req, res, sessionSecret);
      session.userEmail = user.email;
      session.userId = user.id;
      await session.save();

      // Resolve groups
      let groups: string[] = [];
      let isAdmin = false;
      if (resolveGroups) {
        try {
          groups = await resolveGroups(user.email);
          if (adminGroup && groups.includes(adminGroup)) isAdmin = true;
        } catch { /* ignore */ }
      }

      const userResp = await buildUserResponse(user.email, isAdmin, groups, permStore, getAgentIds?.() ?? []);
      res.json({
        user: { id: user.id, email: user.email, name: user.name },
        groups: userResp.groups,
        is_admin: userResp.is_admin,
        agent_levels: userResp.agent_levels,
        dashboard_access: userResp.dashboard_access,
      });
    } catch {
      res.status(401).json({ error: "Invalid email or password" });
    }
  });

  router.post("/logout", async (req, res): Promise<void> => {
    const session = await getSession(req, res, sessionSecret);
    session.destroy();
    res.json({ ok: true });
  });

  router.get("/gateway/logout", async (req, res): Promise<void> => {
    const session = await getSession(req, res, sessionSecret);
    session.destroy();
    res.redirect("/login");
  });

  // --- Current user info ---
  router.get("/me", async (req, res): Promise<void> => {
    const session = await getSession(req, res, sessionSecret);
    if (!session.userEmail) {
      res.status(401).json({ error: "Unauthorized" });
      return;
    }

    const { rows } = await pool.query<{ id: number; email: string; name: string | null }>(
      "SELECT id, email, name FROM users WHERE email = $1",
      [session.userEmail]
    );
    const dbUser = rows[0];

    let groups: string[] = [];
    let isAdmin = session.isAdmin ?? false;
    if (resolveGroups) {
      try {
        groups = await resolveGroups(session.userEmail);
        if (adminGroup && groups.includes(adminGroup)) isAdmin = true;
      } catch { /* ignore */ }
    }

    const userResp = await buildUserResponse(session.userEmail, isAdmin, groups, permStore, getAgentIds?.() ?? []);
    res.json({
      user: {
        id: dbUser?.id ?? session.userId,
        email: userResp.email,
        name: dbUser?.name ?? null,
      },
      groups: userResp.groups,
      is_admin: userResp.is_admin,
      agent_levels: userResp.agent_levels,
      dashboard_access: userResp.dashboard_access,
    });
  });

  // --- Change password ---
  router.post("/change-password", async (req, res): Promise<void> => {
    const session = await getSession(req, res, sessionSecret);
    if (!session.userEmail) {
      res.status(401).json({ error: "Unauthorized" });
      return;
    }

    const schema = z.object({
      currentPassword: z.string().min(1),
      newPassword: z.string().min(8),
    });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { currentPassword, newPassword } = parsed.data;

    // Get current hash
    const { rows } = await pool.query<{ password_hash: string }>(
      "SELECT password_hash FROM users WHERE email = $1",
      [session.userEmail]
    );
    if (rows.length === 0 || !rows[0].password_hash) {
      // User may be gateway-only with no local password
      res.status(400).json({ error: "No local password set. Use LTP gateway to sign in." });
      return;
    }

    const valid = await import("../auth.js").then(m => m.verifyPassword(rows[0].password_hash, currentPassword));
    if (!valid) {
      res.status(400).json({ error: "Current password is incorrect" });
      return;
    }

    const hash = await import("../auth.js").then(m => m.hashPassword(newPassword));
    await pool.query("UPDATE users SET password_hash = $1 WHERE email = $2", [hash, session.userEmail]);

    res.json({ ok: true });
  });

  return router;
}
