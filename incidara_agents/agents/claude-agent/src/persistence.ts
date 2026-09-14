import { mkdirSync, appendFileSync, writeFileSync, readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { GatewayEvent, Session, SessionStatus } from "./types.js";

export type Meta = {
  id: string;
  claudeSessionId?: string;
  status: SessionStatus;
  title?: string;
  workspacePath: string;
  createdAt: string;
  updatedAt: string;
};

export class FsPersistence {
  constructor(private root: string) {
    mkdirSync(root, { recursive: true });
  }

  private sessionDir(id: string): string {
    const dir = join(this.root, id);
    mkdirSync(dir, { recursive: true });
    return dir;
  }

  appendEvent(evt: GatewayEvent): void {
    const file = join(this.sessionDir(evt.session_id), "events.jsonl");
    appendFileSync(file, JSON.stringify(evt) + "\n");
  }

  upsertMeta(s: Session): void {
    const meta: Meta = {
      id: s.id,
      claudeSessionId: s.claudeSessionId,
      status: s.status,
      title: s.title,
      workspacePath: s.workspacePath,
      createdAt: s.createdAt.toISOString(),
      updatedAt: s.updatedAt.toISOString(),
    };
    writeFileSync(join(this.sessionDir(s.id), "meta.json"), JSON.stringify(meta, null, 2));
  }

  listMetas(): Meta[] {
    if (!existsSync(this.root)) return [];
    const out: Meta[] = [];
    for (const dirent of readdirSync(this.root, { withFileTypes: true })) {
      if (!dirent.isDirectory()) continue;
      const metaPath = join(this.root, dirent.name, "meta.json");
      if (!existsSync(metaPath)) continue;
      try {
        out.push(JSON.parse(readFileSync(metaPath, "utf8")));
      } catch {}
    }
    return out;
  }

  readEvents(sessionId: string): GatewayEvent[] {
    const file = join(this.root, sessionId, "events.jsonl");
    if (!existsSync(file)) return [];
    return readFileSync(file, "utf8")
      .split("\n")
      .filter((l) => l.trim())
      .map((l) => JSON.parse(l) as GatewayEvent);
  }
}
