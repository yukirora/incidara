/**
 * Second PG pool for LTP SDK database on .19
 * Host: 192.0.2.10, Port: 5432, DB: openpai, Schema: ltp_sdk
 */
import pg from "pg";

const { Pool } = pg;

const LTP_SDK_URL =
  process.env.LTP_SDK_DB_URL ||
  "postgresql://user:change-me@127.0.0.1:5432/platform";

export const ltpPool = new Pool({
  connectionString: LTP_SDK_URL,
  max: 5,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
});

ltpPool.on("error", (err) => {
  console.error("[ltp-pool] unexpected error:", err.message);
});

export async function queryLtp<T extends pg.QueryResultRow>(
  sql: string,
  params?: unknown[]
): Promise<T[]> {
  const result = await ltpPool.query<T>(sql, params);
  return result.rows;
}
