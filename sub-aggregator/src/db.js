// Postgres pool + query helpers. Tests can swap the implementation via
// setDbImplForTest — ESM bindings are read-only, so we route calls through
// a mutable `impl` object instead of exposing raw functions.
import pg from 'pg';
import { config } from './config.js';
import { logger } from './logger.js';

const { Pool } = pg;

let pool;

function getPool() {
  if (pool) return pool;
  pool = new Pool({
    connectionString: config.pgDsn,
    max: 10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 3_000,
  });
  pool.on('error', (err) => {
    logger.error({ err: err.message }, 'pg_pool_idle_error');
  });
  return pool;
}

// Production implementation. Swappable for tests.
const impl = {
  async getSubPair(token) {
    const q = `
      SELECT token, main_sub_url, gb_sub_url,
             main_user_uuid::text AS main_user_uuid,
             gb_user_uuid::text   AS gb_user_uuid,
             status
      FROM sub_pairs
      WHERE token = $1
      LIMIT 1
    `;
    const res = await getPool().query(q, [token]);
    return res.rows[0] || null;
  },
  async findTokensByUserUuid(uuid) {
    const q = `
      SELECT token FROM sub_pairs
      WHERE main_user_uuid = $1::uuid OR gb_user_uuid = $1::uuid
    `;
    const res = await getPool().query(q, [uuid]);
    return res.rows.map((r) => r.token);
  },
};

/**
 * Read sub_pairs by token. Returns null if not found.
 */
export function getSubPair(token) {
  return impl.getSubPair(token);
}

/**
 * Find tokens affected by a Remnawave user uuid — either as main or as gb.
 */
export function findTokensByUserUuid(uuid) {
  return impl.findTokensByUserUuid(uuid);
}

/**
 * Test hook — replace the DB implementation with mocks. Pass an object
 * with `getSubPair(token)` and `findTokensByUserUuid(uuid)` methods.
 */
export function setDbImplForTest(newImpl) {
  Object.assign(impl, newImpl);
}

export async function closeDb() {
  if (pool) {
    try { await pool.end(); } catch { /* ignore */ }
    pool = null;
  }
}
