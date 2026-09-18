import { describe, it, expect } from "vitest";
import fs from "fs";
import os from "os";
import path from "path";
import { loadAgents } from "./agent-registry.js";

function writeTmpYaml(content: string): string {
  const f = path.join(os.tmpdir(), `agents-test-${Date.now()}.yaml`);
  fs.writeFileSync(f, content);
  return f;
}

describe("loadAgents", () => {
  it("loads a valid agents yaml", () => {
    const f = writeTmpYaml(`
- id: triage-unknown
  name: Triage Unknown Nodes
  description: Triage bot
  gateway_url: http://127.0.0.1:8000
  backend: claude_code
  access:
    owners: [test@example.com]
    groups: []
`);
    const agents = loadAgents(f);
    expect(agents).toHaveLength(1);
    expect(agents[0].id).toBe("triage-unknown");
    expect(agents[0].backend).toBe("claude_code");
    fs.unlinkSync(f);
  });

  it("throws on malformed yaml (not an array)", () => {
    const f = writeTmpYaml(`id: bad\nname: bad`);
    expect(() => loadAgents(f)).toThrow();
    fs.unlinkSync(f);
  });

  it("throws on missing required fields (no gateway_url)", () => {
    const f = writeTmpYaml(`
- id: bad-agent
  name: Bad Agent
  backend: claude_code
  access:
    owners: []
    groups: []
`);
    expect(() => loadAgents(f)).toThrow();
    fs.unlinkSync(f);
  });

  it("throws on invalid backend enum", () => {
    const f = writeTmpYaml(`
- id: bad-agent
  name: Bad Agent
  gateway_url: http://127.0.0.1:8000
  backend: unknown_backend
  access:
    owners: []
    groups: []
`);
    expect(() => loadAgents(f)).toThrow();
    fs.unlinkSync(f);
  });

  it("throws on invalid gateway_url", () => {
    const f = writeTmpYaml(`
- id: bad-agent
  name: Bad Agent
  gateway_url: not-a-url
  backend: claude_code
  access:
    owners: []
    groups: []
`);
    expect(() => loadAgents(f)).toThrow();
    fs.unlinkSync(f);
  });
});

describe("shipped example registry", () => {
  // The deployment mounts config/agents.yaml.example until an operator creates
  // the real file, so the example must satisfy this schema.
  it("loads console/config/agents.yaml.example", () => {
    const example = path.join(__dirname, "..", "..", "config", "agents.yaml.example");
    const agents = loadAgents(example);
    expect(agents.length).toBeGreaterThan(0);
    for (const agent of agents) {
      expect(agent.gateway_url).toMatch(/^https?:\/\/\S+:\d+$/);
      expect(agent.access.owners.length + agent.access.groups.length).toBeGreaterThan(0);
    }
  });
});
