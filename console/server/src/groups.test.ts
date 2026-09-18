import { describe, it, expect } from "vitest";
import fs from "fs";
import os from "os";
import path from "path";
import { loadGroups, resolveUserGroups } from "./groups.js";

function writeTmpYaml(content: string): string {
  const f = path.join(os.tmpdir(), `groups-test-${Date.now()}.yaml`);
  fs.writeFileSync(f, content);
  return f;
}

describe("loadGroups", () => {
  it("loads a valid groups yaml", () => {
    const f = writeTmpYaml(`
- id: sre-team
  name: SRE Team
  members: [alice@example.com, bob@example.com]
`);
    const groups = loadGroups(f);
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("sre-team");
    expect(groups[0].members).toContain("alice@example.com");
    fs.unlinkSync(f);
  });

  it("throws on malformed yaml", () => {
    const f = writeTmpYaml(`id: bad`);
    expect(() => loadGroups(f)).toThrow();
    fs.unlinkSync(f);
  });
});

describe("resolveUserGroups", () => {
  const groups = [
    { id: "sre-team", name: "SRE Team", members: ["alice@example.com", "bob@example.com"] },
    { id: "dev-team", name: "Dev Team", members: ["alice@example.com", "carol@example.com"] },
    { id: "ops-team", name: "Ops Team", members: ["dave@example.com"] },
  ];

  it("returns all groups for a member in multiple groups", () => {
    const result = resolveUserGroups(groups, "alice@example.com");
    expect(result).toContain("sre-team");
    expect(result).toContain("dev-team");
    expect(result).not.toContain("ops-team");
  });

  it("returns single group for member in one group", () => {
    const result = resolveUserGroups(groups, "dave@example.com");
    expect(result).toEqual(["ops-team"]);
  });

  it("returns empty array for non-member", () => {
    const result = resolveUserGroups(groups, "nobody@example.com");
    expect(result).toEqual([]);
  });
});

describe("shipped example groups", () => {
  it("loads console/config/groups.yaml.example", () => {
    const example = path.join(__dirname, "..", "..", "config", "groups.yaml.example");
    expect(loadGroups(example).length).toBeGreaterThan(0);
  });
});
