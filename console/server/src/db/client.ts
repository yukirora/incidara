import pg from "pg";

const { Pool } = pg;

export function createPool(url: string): pg.Pool {
  return new Pool({ connectionString: url });
}
