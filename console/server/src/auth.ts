import argon2 from "argon2";
import type pg from "pg";

export async function hashPassword(password: string): Promise<string> {
  return argon2.hash(password, { type: argon2.argon2id });
}

export async function verifyPassword(hash: string, password: string): Promise<boolean> {
  return argon2.verify(hash, password);
}

export interface UserRow {
  id: string;
  email: string;
  name: string | null;
  created_at: Date;
}

/**
 * Ensure a user exists in the DB — create if not found.
 * Used by auth-gateway callback to auto-provision users.
 */
export async function ensureUser(
  pool: pg.Pool,
  { email, name, isAdmin }: { email: string; name: string; isAdmin?: boolean }
): Promise<UserRow> {
  // Check if user exists
  const { rows } = await pool.query<UserRow>(
    "SELECT id, email, name, created_at FROM users WHERE email = $1",
    [email.toLowerCase().trim()]
  );
  if (rows.length > 0) {
    // Update name and admin status if changed
    if (isAdmin !== undefined) {
      await pool.query(
        "UPDATE users SET name = $1 WHERE email = $2 AND (name IS DISTINCT FROM $1)",
        [name, email.toLowerCase().trim()]
      );
    }
    return rows[0];
  }

  // Auto-create user with a random password (they login via gateway, never need it)
  const randomPassword = crypto.randomUUID();
  const hash = await hashPassword(randomPassword);
  const { rows: newRows } = await pool.query<UserRow>(
    `INSERT INTO users (email, password_hash, name)
     VALUES ($1, $2, $3)
     RETURNING id, email, name, created_at`,
    [email.toLowerCase().trim(), hash, name]
  );
  return newRows[0];
}

export async function signup(
  pool: pg.Pool,
  { email, password, name }: { email: string; password: string; name?: string }
): Promise<UserRow> {
  const hash = await hashPassword(password);
  try {
    const { rows } = await pool.query<UserRow>(
      `INSERT INTO users (email, password_hash, name)
       VALUES ($1, $2, $3)
       RETURNING id, email, name, created_at`,
      [email.toLowerCase().trim(), hash, name ?? null]
    );
    return rows[0];
  } catch (err: unknown) {
    const pgErr = err as { code?: string };
    if (pgErr.code === "23505") {
      throw new Error("Email already registered");
    }
    throw err;
  }
}

export async function login(
  pool: pg.Pool,
  { email, password }: { email: string; password: string }
): Promise<UserRow> {
  const { rows } = await pool.query<UserRow & { password_hash: string }>(
    `SELECT id, email, name, created_at, password_hash
     FROM users WHERE email = $1`,
    [email.toLowerCase().trim()]
  );
  if (rows.length === 0) {
    throw new Error("Invalid email or password");
  }
  const user = rows[0];
  const valid = await verifyPassword(user.password_hash, password);
  if (!valid) {
    throw new Error("Invalid email or password");
  }
  const { password_hash: _h, ...rest } = user;
  return rest as UserRow;
}
