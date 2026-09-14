import type { Request, Response, NextFunction } from "express";
import { getSession } from "../session-cookie.js";
import type { AuthContext } from "../authz.js";

// Augment Express Request to carry userEmail, userId, and auth context
declare global {
  namespace Express {
    interface Request {
      userEmail?: string;
      userId?: string;
      sessionIsAdmin?: boolean;
      auth?: AuthContext;
    }
  }
}

export function requireAuth(secret: string) {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const session = await getSession(req, res, secret);
    if (!session.userEmail) {
      res.status(401).json({ error: "Unauthorized" });
      return;
    }
    req.userEmail = session.userEmail;
    req.userId = session.userId;
    req.sessionIsAdmin = session.isAdmin;
    next();
  };
}
