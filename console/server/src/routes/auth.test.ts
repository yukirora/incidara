import { describe, it, expect, beforeAll, afterAll } from "vitest";
import express from "express";
import { createPool } from "../db/client.js";
import { makeAuthRouter } from "./auth.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";

describe.skipIf(!DB_URL)("Auth routes", () => {
  let pool: pg.Pool;
  let app: express.Application;
  const testEmail = `auth_route_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
  });

  afterAll(async () => {
    await pool.query("DELETE FROM users WHERE email = $1", [testEmail]).catch(() => {});
    await pool.end();
  });

  async function request(
    method: string,
    path: string,
    body?: unknown,
    cookies?: string
  ): Promise<{ status: number; body: unknown; setCookie?: string[] }> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (cookies) headers["Cookie"] = cookies;

    const resp = await fetch(`http://127.0.0.1`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    }).catch(() => null);

    // Use supertest-like approach via direct app invocation
    return new Promise((resolve) => {
      const req = {
        method,
        url: path,
        headers,
        body: body ?? {},
      };

      // Use node http
      const server = app.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        const h: Record<string, string> = { "Content-Type": "application/json" };
        if (cookies) h["Cookie"] = cookies;

        fetch(`http://127.0.0.1:${port}${path}`, {
          method,
          headers: h,
          body: body ? JSON.stringify(body) : undefined,
        }).then(async (r) => {
          const setCookie = r.headers.getSetCookie?.() ?? [];
          const b = await r.json().catch(() => ({}));
          server.close(() => resolve({ status: r.status, body: b, setCookie }));
        });
      });
    });
  }

  it("POST /signup creates user and sets cookie", async () => {
    const { status, body, setCookie } = await request("POST", "/api/auth/signup", {
      email: testEmail,
      password: "testpass1234",
    });
    expect(status).toBe(201);
    const b = body as { user: { email: string } };
    expect(b.user.email).toBe(testEmail);
    expect(setCookie?.some((c: string) => c.includes("incidara_session"))).toBe(true);
  });

  it("POST /signup returns 409 on duplicate", async () => {
    const { status } = await request("POST", "/api/auth/signup", {
      email: testEmail,
      password: "testpass1234",
    });
    expect(status).toBe(409);
  });

  it("POST /login with wrong password returns 401", async () => {
    const { status } = await request("POST", "/api/auth/login", {
      email: testEmail,
      password: "wrongpassword",
    });
    expect(status).toBe(401);
  });

  it("POST /login with correct password sets cookie", async () => {
    const { status, body, setCookie } = await request("POST", "/api/auth/login", {
      email: testEmail,
      password: "testpass1234",
    });
    expect(status).toBe(200);
    const b = body as { user: { email: string } };
    expect(b.user.email).toBe(testEmail);
    expect(setCookie?.some((c: string) => c.includes("incidara_session"))).toBe(true);
  });

  it("GET /me without cookie returns 401", async () => {
    const { status } = await request("GET", "/api/auth/me");
    expect(status).toBe(401);
  });

  it("GET /me with valid cookie returns user", async () => {
    // First login to get cookie
    let loginCookie = "";
    await new Promise<void>((resolve) => {
      const server = app.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: testEmail, password: "testpass1234" }),
        }).then(async (r) => {
          const cookies = r.headers.getSetCookie?.() ?? [];
          loginCookie = cookies.find((c: string) => c.includes("incidara_session")) ?? "";
          server.close(() => resolve());
        });
      });
    });

    // Extract just the cookie value
    const cookieValue = loginCookie.split(";")[0];
    const { status, body } = await request("GET", "/api/auth/me", undefined, cookieValue);
    expect(status).toBe(200);
    const b = body as { user: { email: string } };
    expect(b.user.email).toBe(testEmail);
  });
});
