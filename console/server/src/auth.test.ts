import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createPool } from "./db/client.js";
import { hashPassword, verifyPassword, signup, login } from "./auth.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;

describe("hashPassword / verifyPassword", () => {
  it("hashes and verifies a password", async () => {
    const hash = await hashPassword("mysecretpassword");
    expect(hash).not.toBe("mysecretpassword");
    const valid = await verifyPassword(hash, "mysecretpassword");
    expect(valid).toBe(true);
  });

  it("rejects wrong password", async () => {
    const hash = await hashPassword("correct");
    const valid = await verifyPassword(hash, "wrong");
    expect(valid).toBe(false);
  });
});

describe.skipIf(!DB_URL)("signup + login (DB)", () => {
  let pool: pg.Pool;
  const testEmail = `test_auth_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
  });

  afterAll(async () => {
    await pool.query("DELETE FROM users WHERE email = $1", [testEmail]).catch(() => {});
    await pool.end();
  });

  it("signup creates user and returns row", async () => {
    const user = await signup(pool, { email: testEmail, password: "testpass1234", name: "Test User" });
    expect(user.email).toBe(testEmail);
    expect(user.id).toBeTruthy();
  });

  it("signup throws on duplicate email", async () => {
    await expect(signup(pool, { email: testEmail, password: "other" })).rejects.toThrow(
      "Email already registered"
    );
  });

  it("login succeeds with correct password", async () => {
    const user = await login(pool, { email: testEmail, password: "testpass1234" });
    expect(user.email).toBe(testEmail);
  });

  it("login fails with wrong password", async () => {
    await expect(login(pool, { email: testEmail, password: "wrongpass" })).rejects.toThrow(
      "Invalid email or password"
    );
  });

  it("login fails with nonexistent email", async () => {
    await expect(login(pool, { email: "nobody@nowhere.com", password: "pass" })).rejects.toThrow(
      "Invalid email or password"
    );
  });
});
