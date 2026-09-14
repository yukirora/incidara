import { Router } from "express";
import type { Group } from "../groups.js";

export function groupsRouter({ getGroups }: { getGroups: () => Group[] }): Router {
  const r = Router();
  r.get("/", (_req, res) => {
    res.json({ groups: getGroups().map((g) => ({ id: g.id, name: g.name })) });
  });
  return r;
}
