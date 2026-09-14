import type { Agent } from "./agent-registry.js";
import { PermissionStore, type AccessLevel } from "./permission-store.js";

// ─── Types ───────────────────────────────────────────────────────────

export interface SessionLike {
  owner_email: string;
  shared_with_groups: string[];
}

// ─── AuthContext ─────────────────────────────────────────────────────

/**
 * Encapsulates all auth state for a single request.
 * Created once per request by attachAuthContext middleware.
 * Routes use this — never touch groups/adminGroup directly.
 */
export class AuthContext {
  /** Lazy-loaded, cached per request for performance */
  private _agentLevels: Record<string, AccessLevel> | null = null;
  private _dashboardAccess: Record<string, boolean> | null = null;
  /** Promise of the first async load — reused so concurrent calls wait once */
  private _loadPromise: Promise<void> | null = null;

  constructor(
    public readonly email: string,
    public readonly groups: string[],
    public readonly adminGroup: string | undefined,
    private readonly _gatewayAdmin: boolean,
    private readonly _permStore: PermissionStore,
  ) {}

  get isAdmin(): boolean {
    if (this._gatewayAdmin) return true;
    return !!this.adminGroup && this.groups.includes(this.adminGroup);
  }

  // ─── Permission resolution ──────────────────────────────────────────

  /** Load permissions (idempotent, safe to call multiple times). */
  private async _ensurePermissions(): Promise<void> {
    if (this._agentLevels) return; // already loaded
    if (this._loadPromise) {
      await this._loadPromise;
      return;
    }
    this._loadPromise = Promise.all([
      this._permStore.getUserAgentLevels(this.email),
      this._permStore.getUserDashboardAccess(this.email),
    ]).then(([agentLevels, dashAccess]) => {
      this._agentLevels = agentLevels;
      this._dashboardAccess = dashAccess;
    });
    await this._loadPromise;
  }

  /**
   * User's resolved access level for an agent.
   * Returns null if no access (agent hidden from this user).
   */
  async agentLevel(agentId: string): Promise<AccessLevel | null> {
    if (this.isAdmin) return "interactive";
    await this._ensurePermissions();
    return this._agentLevels![agentId] ?? null;
  }

  /**
   * Whether user can access a dashboard.
   * Returns null (not in map) if no access.
   */
  async dashboardAllowed(dashboard: string): Promise<boolean | null> {
    if (this.isAdmin) return true;
    await this._ensurePermissions();
    return this._dashboardAccess![dashboard] ?? null;
  }

  /** All agent levels for this user (for passing to frontend). */
  async getAgentLevels(): Promise<Record<string, AccessLevel>> {
    await this._ensurePermissions();
    return this._agentLevels!;
  }

  /** All dashboard access for this user (for passing to frontend). */
  async getDashboardAccess(): Promise<Record<string, boolean>> {
    await this._ensurePermissions();
    return this._dashboardAccess!;
  }

  // ─── Convenience checks ─────────────────────────────────────────────

  /** True if user can send messages to this agent */
  async canSendToAgent(agent: Agent): Promise<boolean> {
    const level = await this.agentLevel(agent.id);
    return level === "interactive";
  }

  // ─── Agent-level (legacy YAML-based, still used for initial filter) ─

  /** Legacy check: does YAML config allow access? Used for initial agent visibility. */
  async canAccessAgent(agent: Agent): Promise<boolean> {
    if (this.isAdmin) return true;
    const e = this.email.toLowerCase().trim();
    if (agent.access.owners.some((o) => o.toLowerCase().trim() === e)) return true;
    if (agent.access.groups.some((g) => this.groups.includes(g))) return true;
    // Also check DB-based permission if no YAML access
    const dbLevel = await this.agentLevel(agent.id);
    return dbLevel !== null && dbLevel !== undefined;
  }

  /** What level of access does this user have on this agent? */
  async agentAccessLevel(agent: Agent): Promise<"admin" | "owner" | "group" | "none"> {
    if (this.isAdmin) return "admin";
    const e = this.email.toLowerCase().trim();
    if (agent.access.owners.some((o) => o.toLowerCase().trim() === e)) return "owner";
    if (agent.access.groups.some((g) => this.groups.includes(g))) return "group";
    // Check DB
    const dbLevel = await this.agentLevel(agent.id);
    if (dbLevel !== null && dbLevel !== undefined) return "group"; // DB-based access
    return "none";
  }

  // ─── Session-level ──────────────────────────────────────────────────

  /**
   * Can this user view the session?
   * - Admins can see all sessions
   * - Users can always see their own sessions
   * - Users with agent access (any level > no_access) can see ALL sessions on that agent
   */
  async canViewSession(session: SessionLike, agent?: Agent): Promise<boolean> {
    if (this.isAdmin) return true;
    const e = this.email.toLowerCase().trim();
    if (session.owner_email.toLowerCase().trim() === e) return true;
    if (agent) {
      const level = await this.agentLevel(agent.id);
      if (level !== null && level !== undefined) return true;
    }
    return false;
  }

  /**
   * Can this user act as session owner?
   * (send messages, approve, interrupt, resume, delete, share)
   * - Admins always yes
   * - Only the actual session owner
   */
  isSessionOwner(session: SessionLike): boolean {
    if (this.isAdmin) return true;
    return session.owner_email.toLowerCase().trim() === this.email.toLowerCase().trim();
  }

  // ─── Task-level (inherits from session) ─────────────────────────────

  /**
   * Can this user complete/cancel this task?
   * - Admins always yes
   * - Only the task's submitter
   */
  isTaskOwner(submitterEmail: string): boolean {
    if (this.isAdmin) return true;
    return submitterEmail.toLowerCase().trim() === this.email.toLowerCase().trim();
  }
}
