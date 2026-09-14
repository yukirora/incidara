import { getIronSession } from "iron-session";
import type { Request, Response } from "express";

export interface SessionData {
  userEmail?: string;
  userId?: string;
  isAdmin?: boolean;
}

export async function getSession(req: Request, res: Response, secret: string) {
  return getIronSession<SessionData>(req, res, {
    cookieName: "incidara_session",
    password: secret,
    cookieOptions: {
      secure: process.env.NODE_ENV === "production",
      httpOnly: true,
      sameSite: "lax",
    },
  });
}
