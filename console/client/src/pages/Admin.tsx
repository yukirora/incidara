import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useMe } from "../hooks/useMe.ts";
import { AdminUsage } from "./AdminUsage.tsx";
import {
  listAdminUsers,
  deleteAdminUser,
  createAdminUser,
  listAdminGroups,
  createAdminGroup,
  deleteAdminGroup,
  listAdminAgents,
  addGroupMember,
  removeGroupMember,
  addAgentGroupAccess,
  removeAgentGroupAccess,
  updateAgentGroupLevel,
  getDashboardPermissions,
  setDashboardPermission,
  type AdminUser,
  type AdminGroup,
  type AdminAgent,
} from "../api/admin.ts";
import { ApiError } from "../lib/api.ts";
import type { AccessLevel } from "../api/auth.ts";

type Tab = "users" | "groups" | "agents" | "dashboards" | "usage";

// ── Access Level helpers ───────────────────────────────────────────────────

const ACCESS_LEVELS: AccessLevel[] = ["interactive", "readonly", "readonly_input", "readonly_summary"];

const LEVEL_COLORS: Record<AccessLevel, string> = {
  interactive: "bg-emerald-50 text-emerald-700",
  readonly: "bg-blue-50 text-blue-700",
  readonly_input: "bg-orange-50 text-orange-700",
  readonly_summary: "bg-violet-50 text-violet-700",
};

function LevelBadge({ level }: { level: string }) {
  const l = level as AccessLevel;
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold ${LEVEL_COLORS[l] ?? "bg-zinc-100 text-zinc-600"}`}>
      {level}
    </span>
  );
}

// ── Confirm Modal ──────────────────────────────────────────────────────────

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmModal({ title, message, confirmLabel = "Confirm", danger = false, onConfirm, onCancel }: ConfirmModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-white rounded-xl shadow-xl border border-zinc-200 w-96 max-w-[95vw] p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-zinc-900 mb-1.5">{title}</h3>
        <p className="text-xs text-zinc-500 mb-4">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors">Cancel</button>
          <button onClick={onConfirm} className={`px-3 py-1.5 text-xs font-semibold text-white rounded-lg transition-colors ${danger ? "bg-red-500 hover:bg-red-600" : "bg-zinc-900 hover:bg-zinc-700"}`}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Create User Modal ──────────────────────────────────────────────────────

function CreateUserModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [group, setGroup] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => createAdminUser({ email, password, name: name || undefined, groups: group ? [group] : undefined }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to create user");
      }
    },
  });

  const passwordError = password.length > 0 && password.length < 8 ? "Min 8 characters" : confirm.length > 0 && password !== confirm ? "Passwords don't match" : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl border border-zinc-200 w-[400px] max-w-[95vw] p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-zinc-900 mb-0.5">Create user</h3>
        <p className="text-xs text-zinc-500 mb-4">Create a new account with a password</p>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-zinc-700 mb-1">Email address *</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@company.com"
              className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-zinc-700 mb-1">Display name <span className="font-normal text-zinc-400">(optional)</span></label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe"
              className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Password *</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Min 8 characters"
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Confirm *</label>
              <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Repeat password"
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400" />
            </div>
          </div>
          {passwordError && <p className="text-[11px] text-red-500 -mt-1">{passwordError}</p>}
          <div>
            <label className="block text-xs font-semibold text-zinc-700 mb-1">Add to group <span className="font-normal text-zinc-400">(optional)</span></label>
            <select value={group} onChange={(e) => setGroup(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 bg-white">
              <option value="">No groups</option>
              <option value="admins">admins</option>
              <option value="sre-team">sre-team</option>
              <option value="dev-team">dev-team</option>
              <option value="readonly-input-team">readonly-input-team</option>
              <option value="readonly-summary-team">readonly-summary-team</option>
            </select>
            <p className="text-[10px] text-zinc-400 mt-1">Default access level for new group membership: <strong>interactive</strong></p>
          </div>
        </div>

        {error && <p className="mt-3 text-xs text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors">Cancel</button>
          <button
            onClick={() => mutation.mutate()}
            disabled={!email || !password || password.length < 8 || password !== confirm || mutation.isPending}
            className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50"
          >
            {mutation.isPending ? "Creating..." : "Create user"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Add Member Modal ───────────────────────────────────────────────────────

function AddMemberModal({ groupId, onClose }: { groupId: string; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => addGroupMember(groupId, email),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) setError(err.message);
      else setError("Failed to add member");
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl border border-zinc-200 w-80 max-w-[95vw] p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-zinc-900 mb-0.5">Add member</h3>
        <p className="text-xs text-zinc-500 mb-3">to <span className="font-mono text-zinc-700">{groupId}</span></p>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@company.com"
          className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 mb-2"
          onKeyDown={(e) => { if (e.key === "Enter") mutation.mutate(); }} />
        {error && <p className="text-xs text-red-500 mb-2">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors">Cancel</button>
          <button onClick={() => mutation.mutate()} disabled={!email || mutation.isPending}
            className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50">
            {mutation.isPending ? "Adding..." : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Create Group Modal ─────────────────────────────────────────────────────

function CreateGroupModal({ onClose }: { onClose: () => void }) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => createAdminGroup(id, name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) setError(err.message);
      else setError("Failed to create group");
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl border border-zinc-200 w-80 max-w-[95vw] p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-zinc-900 mb-3">Create group</h3>
        <div className="space-y-2 mb-3">
          <input type="text" value={id} onChange={(e) => setId(e.target.value.toLowerCase())}
            placeholder="group-id" className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 font-mono" />
          <input type="text" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Display name" className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400" />
        </div>
        {error && <p className="text-xs text-red-500 mb-2">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors">Cancel</button>
          <button onClick={() => mutation.mutate()} disabled={!id || !name || mutation.isPending}
            className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50">
            {mutation.isPending ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Add Group to Agent Modal ───────────────────────────────────────────────

function AddAgentGroupModal({ agentId, agentName, existingGroups, allGroups, onClose }: {
  agentId: string; agentName: string; existingGroups: string[]; allGroups: AdminGroup[]; onClose: () => void;
}) {
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [accessLevel, setAccessLevel] = useState<AccessLevel>("interactive");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const availableGroups = allGroups.filter((g) => !existingGroups.includes(g.id));

  const addMutation = useMutation({
    mutationFn: async () => {
      await addAgentGroupAccess(agentId, selectedGroupId);
      await updateAgentGroupLevel(agentId, selectedGroupId, accessLevel);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "agents"] });
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) setError(err.message);
      else setError("Failed to add group access");
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl border border-zinc-200 w-80 max-w-[95vw] p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-zinc-900 mb-3">Add group to {agentName}</h3>
        {availableGroups.length === 0 ? (
          <p className="text-xs text-zinc-500">All groups already have access.</p>
        ) : (
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Group</label>
              <select value={selectedGroupId} onChange={(e) => setSelectedGroupId(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 bg-white">
                <option value="">Select group...</option>
                {availableGroups.map((g) => <option key={g.id} value={g.id}>{g.name} ({g.id})</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Access level</label>
              <select value={accessLevel} onChange={(e) => setAccessLevel(e.target.value as AccessLevel)}
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 bg-white">
                {ACCESS_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
          </div>
        )}
        {error && <p className="text-xs text-red-500 mt-2">{error}</p>}
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors">Cancel</button>
          <button onClick={() => addMutation.mutate()} disabled={!selectedGroupId || addMutation.isPending}
            className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50">
            {addMutation.isPending ? "Adding..." : "Add access"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Users Tab ──────────────────────────────────────────────────────────────

function UsersTab() {
  const { user: currentUser } = useMe();
  const queryClient = useQueryClient();
  const { data: users, isLoading, error } = useQuery<AdminUser[]>({ queryKey: ["admin", "users"], queryFn: listAdminUsers });
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<AdminUser | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (email: string) => deleteAdminUser(email),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      setConfirmDelete(null);
    },
  });

  if (isLoading) return <div className="py-8 text-center text-zinc-400">Loading...</div>;
  if (error) return <div className="py-8 text-center text-red-500">Failed to load users</div>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <p className="text-xs text-zinc-500">{users?.length ?? 0} users total</p>
        <button onClick={() => setCreating(true)} className="text-xs px-3 py-1.5 bg-zinc-900 text-white rounded-lg hover:bg-zinc-700 transition-colors font-medium">
          + Create user
        </button>
      </div>

      {/* Access level legend */}
      <div className="flex flex-wrap gap-3 mb-4 p-2.5 bg-zinc-50 border border-zinc-200 rounded-lg text-[11px] text-zinc-500">
        {ACCESS_LEVELS.map((l) => (
          <span key={l} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded ${LEVEL_COLORS[l]}`}>
            <LevelBadge level={l} />
            <span className="text-[10px]">{l === "interactive" ? "full view + actions" : l === "readonly" ? "full view, no actions" : l === "readonly_summary" ? "input + summary only" : "prompt + tool names"}</span>
          </span>
        ))}
      </div>

      <div className="border border-zinc-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 border-b border-zinc-200">
            <tr>
              <th className="text-left py-2 px-4 text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">Email</th>
              <th className="text-left py-2 px-4 text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">Name</th>
              <th className="text-left py-2 px-4 text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">Joined</th>
              <th className="py-2 px-4" />
            </tr>
          </thead>
          <tbody>
            {users?.map((user) => {
              const isSelf = user.email === currentUser?.email;
              return (
                <tr key={user.id} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50/60">
                  <td className="py-2.5 px-4 text-zinc-800 font-medium text-[13px]">{user.email}</td>
                  <td className="py-2.5 px-4 text-zinc-500 text-[13px]">{user.name ?? <span className="text-zinc-300">—</span>}</td>
                  <td className="py-2.5 px-4 text-zinc-400 text-xs">{new Date(user.created_at).toLocaleDateString()}</td>
                  <td className="py-2.5 px-4 text-right">
                    <button onClick={() => !isSelf && setConfirmDelete(user)}
                      disabled={isSelf} title={isSelf ? "Cannot delete yourself" : `Delete ${user.email}`}
                      className="text-xs text-red-500 hover:text-red-700 disabled:opacity-30 disabled:cursor-not-allowed">
                      Delete
                    </button>
                  </td>
                </tr>
              );
            })}
            {(!users || users.length === 0) && (
              <tr><td colSpan={4} className="py-8 text-center text-zinc-400 text-sm">No users found</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && <CreateUserModal onClose={() => setCreating(false)} />}
      {confirmDelete && (
        <ConfirmModal title="Delete user" message={`Delete ${confirmDelete.email}? All their sessions and tasks will be removed.`}
          confirmLabel="Delete" danger onConfirm={() => deleteMutation.mutate(confirmDelete.email)} onCancel={() => setConfirmDelete(null)} />
      )}
    </div>
  );
}

// ── Groups Tab ─────────────────────────────────────────────────────────────


// Will be replaced by API response, but keep as fallback

function GroupsTab({ agents }: { agents: AdminAgent[] }) {
  const queryClient = useQueryClient();
  const { data: groups, isLoading, error } = useQuery<AdminGroup[]>({ queryKey: ["admin", "groups"], queryFn: listAdminGroups });
  const { data: dashData } = useQuery({ queryKey: ["admin", "dashboards"], queryFn: getDashboardPermissions });
  const dashboardPerms = dashData?.permissions;
  const dashboards = dashData?.dashboards ?? [];
  const [addingToGroup, setAddingToGroup] = useState<string | null>(null);
  const [removingKey, setRemovingKey] = useState<string | null>(null);
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [confirmDeleteGroup, setConfirmDeleteGroup] = useState<AdminGroup | null>(null);

  const removeMutation = useMutation({
    mutationFn: ({ groupId, email }: { groupId: string; email: string }) => removeGroupMember(groupId, email),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      setRemovingKey(null);
    },
  });

  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: string) => deleteAdminGroup(groupId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "groups"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "agents"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "dashboards"] });
      setConfirmDeleteGroup(null);
    },
  });

  const dashMutation = useMutation({
    mutationFn: ({ groupId, dashboard, allowed }: { groupId: string; dashboard: string; allowed: boolean }) =>
      setDashboardPermission(groupId, dashboard, allowed),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "dashboards"] });
    },
  });

  // Build agent lookup: which agents reference each group
  const groupToAgents: Record<string, string[]> = {};
  for (const agent of agents) {
    for (const g of agent.access.groups) {
      if (!groupToAgents[g]) groupToAgents[g] = [];
      groupToAgents[g].push(agent.name);
    }
  }

  // Build dashboard permission lookup: { groupId: { dashboard: allowed } }
  const dashMap: Record<string, Record<string, boolean>> = {};
  for (const p of dashboardPerms ?? []) {
    if (!dashMap[p.group_id]) dashMap[p.group_id] = {};
    dashMap[p.group_id][p.dashboard] = p.allowed;
  }

  if (isLoading) return <div className="py-8 text-center text-zinc-400">Loading...</div>;
  if (error) return <div className="py-8 text-center text-red-500">Failed to load groups</div>;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setCreatingGroup(true)} className="text-xs px-3 py-1.5 bg-zinc-900 text-white rounded-lg hover:bg-zinc-700 font-medium transition-colors">
          + Create group
        </button>
      </div>

      {groups?.map((group) => {
        const agentsUsingGroup = groupToAgents[group.id] ?? [];
        const isYamlGroup = group.source === "yaml";
        return (
          <div key={group.id} className="border border-zinc-200 rounded-xl overflow-hidden">
            {/* Group header */}
            <div className="flex items-center justify-between px-4 py-3 bg-white">
              <div className="flex items-center gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-zinc-900">{group.name}</h3>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${isYamlGroup ? "bg-zinc-100 text-zinc-500" : "bg-indigo-50 text-indigo-600"}`}>
                      {isYamlGroup ? "yaml" : "custom"}
                    </span>
                  </div>
                  <p className="text-[11px] text-zinc-400 font-mono">{group.id}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => setAddingToGroup(group.id)}
                  className="text-xs px-2.5 py-1 bg-zinc-100 hover:bg-zinc-200 text-zinc-700 rounded-lg transition-colors font-medium">
                  + Add member
                </button>
                {group.can_delete ? (
                  <button onClick={() => setConfirmDeleteGroup(group)} className="text-xs text-red-500 hover:text-red-700">Delete group</button>
                ) : (
                  <span className="text-xs text-zinc-300" title="Cannot delete YAML groups">Delete group</span>
                )}
              </div>
            </div>

            {/* Agents using this group */}
            {agentsUsingGroup.length > 0 && (
              <div className="px-4 py-2 bg-zinc-50 border-b border-zinc-100">
                <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider mb-1">Used by agents</p>
                <div className="flex flex-wrap gap-1">
                  {agentsUsingGroup.map((name) => (
                    <span key={name} className="text-xs px-2 py-0.5 bg-white border border-zinc-200 text-zinc-600 rounded-full">{name}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Members */}
            <div className="px-4 py-2">
              <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">Members ({group.members.length})</p>
              {group.members.length === 0 && <p className="text-xs text-zinc-400">No members</p>}
              <div className="space-y-0.5">
                {group.members.map((member) => {
                  const key = `${group.id}:${member.email}`;
                  return (
                    <div key={member.email} className="flex items-center justify-between py-1.5 border-b border-zinc-50 last:border-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] text-zinc-700">{member.email}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${member.source === "yaml" ? "bg-zinc-100 text-zinc-500" : "bg-emerald-50 text-emerald-600"}`}>
                          {member.source === "yaml" ? "yaml" : "db"}
                        </span>
                        {member.added_by && <span className="text-[10px] text-zinc-400">by {member.added_by}</span>}
                      </div>
                      {member.source === "db" && (
                        <button onClick={() => { setRemovingKey(key); removeMutation.mutate({ groupId: group.id, email: member.email }); }}
                          disabled={removingKey === key && removeMutation.isPending}
                          className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50">
                          {removingKey === key && removeMutation.isPending ? "Removing..." : "Remove"}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Dashboard permissions — custom groups only */}
            {!isYamlGroup && (
              <div className="px-4 py-3 bg-zinc-50 border-t border-zinc-100">
                <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">Dashboard Access</p>
                <div className="flex flex-wrap gap-4">
                  {dashboards.length > 0 ? dashboards.map((dash) => {
                    const allowed = dashMap[group.id]?.[dash.id] ?? false;
                    return (
                      <label key={dash.id} className="flex items-center gap-1.5 text-xs text-zinc-600 cursor-pointer">
                        <input type="checkbox" checked={allowed} onChange={(e) => dashMutation.mutate({ groupId: group.id, dashboard: dash.id, allowed: e.target.checked })}
                          className="w-3.5 h-3.5 accent-zinc-900 rounded cursor-pointer" />
                        {dash.label}
                      </label>
                    );
                  }) : null}
                </div>
                {dashMutation.isPending && <p className="text-[10px] text-zinc-400 mt-1">Saving...</p>}
              </div>
            )}
          </div>
        );
      })}

      {(!groups || groups.length === 0) && <div className="py-8 text-center text-zinc-400">No groups configured</div>}

      {addingToGroup && <AddMemberModal groupId={addingToGroup} onClose={() => setAddingToGroup(null)} />}
      {creatingGroup && <CreateGroupModal onClose={() => setCreatingGroup(false)} />}
      {confirmDeleteGroup && (
        <ConfirmModal title="Delete group"
          message={`Delete "${confirmDeleteGroup.name}"? All memberships and agent access for this group will be removed.`}
          confirmLabel="Delete group" danger
          onConfirm={() => deleteGroupMutation.mutate(confirmDeleteGroup.id)} onCancel={() => setConfirmDeleteGroup(null)} />
      )}
    </div>
  );
}

// ── Agents Tab ─────────────────────────────────────────────────────────────

function AgentsTab({ agents, isLoading, allGroups }: {
  agents: AdminAgent[]; isLoading: boolean; allGroups: AdminGroup[];
}) {
  const queryClient = useQueryClient();
  const [addingGroupToAgent, setAddingGroupToAgent] = useState<AdminAgent | null>(null);
  const [removingKey, setRemovingKey] = useState<string | null>(null);

  const removeGroupMutation = useMutation({
    mutationFn: ({ agentId, groupId }: { agentId: string; groupId: string }) => removeAgentGroupAccess(agentId, groupId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "agents"] });
      setRemovingKey(null);
    },
  });

  const levelMutation = useMutation({
    mutationFn: ({ agentId, groupId, level }: { agentId: string; groupId: string; level: string }) =>
      updateAgentGroupLevel(agentId, groupId, level),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "agents"] });
    },
  });

  if (isLoading) return <div className="py-8 text-center text-zinc-400">Loading...</div>;

  return (
    <div className="space-y-4">
      {agents.map((agent) => (
        <div key={agent.id} className="border border-zinc-200 rounded-xl overflow-hidden">
          {/* Agent header */}
          <div className="flex items-start justify-between px-4 py-3 bg-white">
            <div>
              <h3 className="text-sm font-semibold text-zinc-900">{agent.name}</h3>
              <p className="text-[11px] text-zinc-400">{agent.id} &middot; {agent.backend}</p>
              {agent.description && <p className="text-xs text-zinc-500 mt-1">{agent.description}</p>}
            </div>
            <button onClick={() => setAddingGroupToAgent(agent)}
              className="text-xs px-2.5 py-1 bg-zinc-100 hover:bg-zinc-200 text-zinc-700 rounded-lg transition-colors font-medium shrink-0 ml-3">
              + Add group
            </button>
          </div>

          {/* Group access with level selectors */}
          <div className="px-4 pb-3">
            {agent.access.groups.length === 0 && (
              <p className="text-xs text-zinc-400 py-2">No groups have access</p>
            )}
            {agent.access.groups.map((groupId) => {
              const isYaml = agent.access.yaml_groups.includes(groupId);
              const key = `${agent.id}:${groupId}`;
              const level = agent.group_levels[groupId] ?? "interactive";
              return (
                <div key={groupId} className="flex items-center justify-between py-2 border-b border-zinc-50 last:border-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-zinc-700">{groupId}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${isYaml ? "bg-zinc-100 text-zinc-500" : "bg-indigo-50 text-indigo-600"}`}>
                      {isYaml ? "yaml" : "db"}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* Level selector */}
                    <select
                      value={level}
                      onChange={(e) => levelMutation.mutate({ agentId: agent.id, groupId, level: e.target.value })}
                      className={`text-[11px] font-semibold px-2 py-1 rounded-lg border-0 cursor-pointer focus:outline-none focus:ring-1 focus:ring-zinc-400 ${
                        LEVEL_COLORS[level as AccessLevel] ?? "bg-zinc-100 text-zinc-600"
                      }`}
                      style={{ minWidth: "120px" }}
                    >
                      {ACCESS_LEVELS.map((l) => (
                        <option key={l} value={l}>{l}</option>
                      ))}
                    </select>
                    {/* Remove button for DB groups */}
                    {!isYaml && (
                      <button onClick={() => { setRemovingKey(key); removeGroupMutation.mutate({ agentId: agent.id, groupId }); }}
                        disabled={removingKey === key && removeGroupMutation.isPending}
                        className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50 px-1" title="Remove group access">
                        x
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      {agents.length === 0 && <div className="py-8 text-center text-zinc-400">No agents configured</div>}

      {addingGroupToAgent && (
        <AddAgentGroupModal agentId={addingGroupToAgent.id} agentName={addingGroupToAgent.name}
          existingGroups={addingGroupToAgent.access.groups} allGroups={allGroups}
          onClose={() => setAddingGroupToAgent(null)} />
      )}
    </div>
  );
}

// ── Dashboards Tab ─────────────────────────────────────────────────────────

function DashboardsTab({ groups }: { groups: AdminGroup[] }) {
  const queryClient = useQueryClient();
  const { data: dashData } = useQuery({ queryKey: ["admin", "dashboards"], queryFn: getDashboardPermissions });
  const dashboardPerms = dashData?.permissions;
  const dashboards = dashData?.dashboards ?? [];

  const dashMutation = useMutation({
    mutationFn: ({ groupId, dashboard, allowed }: { groupId: string; dashboard: string; allowed: boolean }) =>
      setDashboardPermission(groupId, dashboard, allowed),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["admin", "dashboards"] }); },
  });

  // Build lookup: { groupId: { dashboard: allowed } }
  const dashMap: Record<string, Record<string, boolean>> = {};
  for (const p of dashboardPerms ?? []) {
    if (!dashMap[p.group_id]) dashMap[p.group_id] = {};
    dashMap[p.group_id][p.dashboard] = p.allowed;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {dashboards.length > 0 ? dashboards.map((dash) => {
          const meta = { icon: "📊", desc: dash.desc };
          return (
            <div key={dash.id} className="border border-zinc-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 bg-white">
                <div className="text-lg mb-0.5">{meta.icon}</div>
                <h3 className="text-sm font-semibold text-zinc-900">{dash.label} Report</h3>
                <p className="text-[11px] text-zinc-500 mt-0.5">{meta.desc}</p>
              </div>
              <div className="px-4 py-2.5 bg-zinc-50 border-t border-zinc-100 space-y-1.5">
                {groups.map((group) => {
                  const isCustom = group.source === "db";
                  const allowed = dashMap[group.id]?.[dash.id] ?? false;
                  return (
                    <div key={group.id} className="flex items-center justify-between">
                      <span className={`text-xs ${isCustom ? "text-zinc-700 font-medium" : "text-zinc-400"}`}>
                        {group.name}
                        {!isCustom && <span className="text-[10px] ml-1 text-zinc-300">(yaml)</span>}
                      </span>
                      <input type="checkbox" checked={allowed} onChange={(e) => dashMutation.mutate({ groupId: group.id, dashboard: dash.id, allowed: e.target.checked })}
                        disabled={!isCustom}
                        className="w-3.5 h-3.5 accent-zinc-900 rounded cursor-pointer disabled:cursor-not-allowed disabled:accent-zinc-300" title={isCustom ? `${dash.label}: ${allowed ? "enabled" : "disabled"}` : "YAML groups: read-only"} />
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }) : null}
      </div>
      {dashMutation.isPending && <p className="text-xs text-zinc-400 text-center">Saving...</p>}
    </div>
  );
}

// ── Main Admin Page ────────────────────────────────────────────────────────

export function Admin() {
  const { is_admin, isLoading: meLoading } = useMe();
  const [activeTab, setActiveTab] = useState<Tab>("users");

  const { data: agents = [], isLoading: agentsLoading } = useQuery<AdminAgent[]>({
    queryKey: ["admin", "agents"],
    queryFn: listAdminAgents,
    enabled: is_admin,
  });

  const { data: groups = [] } = useQuery<AdminGroup[]>({
    queryKey: ["admin", "groups"],
    queryFn: listAdminGroups,
    enabled: is_admin,
  });

  if (meLoading) {
    return <div className="flex items-center justify-center min-h-screen"><span className="text-zinc-400">Loading...</span></div>;
  }
  if (!is_admin) {
    return <Navigate to="/" replace />;
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "users", label: "Users" },
    { id: "groups", label: "Groups" },
    { id: "agents", label: "Agents" },
    { id: "dashboards", label: "Dashboards" },
    { id: "usage", label: "Usage" },
  ];

  return (
    <div className="max-w-5xl mx-auto py-8 px-4">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-zinc-900">Admin Settings</h1>
        <p className="text-xs text-zinc-500 mt-0.5">Manage users, groups, agent permissions, and dashboard access</p>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-zinc-200 mb-6 overflow-x-auto">
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
              activeTab === tab.id ? "border-zinc-900 text-zinc-900" : "border-transparent text-zinc-500 hover:text-zinc-700"
            }`}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "users" && <UsersTab />}
      {activeTab === "groups" && <GroupsTab agents={agents} />}
      {activeTab === "agents" && <AgentsTab agents={agents} isLoading={agentsLoading} allGroups={groups} />}
      {activeTab === "dashboards" && <DashboardsTab groups={groups} />}
      {activeTab === "usage" && <AdminUsage embedded />}
    </div>
  );
}
