import { describe, it, expect, beforeAll, afterAll } from "vitest";
import fs from "fs";
import os from "os";
import path from "path";
import { createPool } from "./client.js";
import { runMigrations } from "./migrate.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;

describe.skipIf(!DB_URL)("runMigrations", () => {
  let pool: pg.Pool;
  let tmpDir: string;
  const testTable = `_mig_test_${Date.now()}`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "mig-test-"));
  });

  afterAll(async () => {
    // Clean up test migration records and test table
    await pool.query(`DELETE FROM _migrations WHERE filename LIKE 'test_%'`).catch(() => {});
    await pool.query(`DROP TABLE IF EXISTS ${testTable}`).catch(() => {});
    fs.rmSync(tmpDir, { recursive: true, force: true });
    await pool.end();
  });

  it("applies a new migration file", async () => {
    const migFile = path.join(tmpDir, `test_001_create_${testTable}.sql`);
    fs.writeFileSync(migFile, `CREATE TABLE IF NOT EXISTS ${testTable} (id SERIAL PRIMARY KEY)`);

    await runMigrations(pool, tmpDir);

    const { rows } = await pool.query(`SELECT to_regclass('public.${testTable}')`);
    expect(rows[0].to_regclass).toBe(testTable);
  });

  it("is idempotent — re-running does not fail", async () => {
    // Second run should skip the already-applied file
    await expect(runMigrations(pool, tmpDir)).resolves.not.toThrow();
  });

  it("skips already-applied migrations", async () => {
    // Verify the migration is recorded
    const { rows } = await pool.query(
      "SELECT filename FROM _migrations WHERE filename LIKE 'test_%'"
    );
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("handles empty migrations directory", async () => {
    const emptyDir = fs.mkdtempSync(path.join(os.tmpdir(), "mig-empty-"));
    try {
      await expect(runMigrations(pool, emptyDir)).resolves.not.toThrow();
    } finally {
      fs.rmSync(emptyDir, { recursive: true, force: true });
    }
  });
});
