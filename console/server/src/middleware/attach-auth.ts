import type { Request, Response, NextFunction } from "express";
import { AuthContext } from "../authz.js";
import type { Group } from "../groups.js";
import type { PermissionStore } from "../permission-store.js";

/**
 * Middleware that creates an AuthContext and attaches it to req.auth.
 * Must be used after requireAuth (which sets req.userEmail).
 *
 * Usage:
 *   app.use(requireAuth(secret));
 *   app.use(attachAuthContext(getGroupsFn, resolveGroupsFn, adminGroup, permStore));
 */
export function attachAuthContext(
  getYamlGroups: () => Group[],
  resolveGroupsFn: (email: string) => Promise<string[]>,
  adminGroup: string | undefined,
  permStore: PermissionStore,
) {
  return async (req: Request, _res: Response, next: NextFunction): Promise<void> => {
    const email = req.userEmail!;
    const groups = await resolveGroupsFn(email);
    req.auth = new AuthContext(email, groups, adminGroup, req.sessionIsAdmin ?? false, permStore);
    next();
  };
}
