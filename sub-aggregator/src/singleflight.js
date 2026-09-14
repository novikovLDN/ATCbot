// FR-6: schlop параллельные сборки одного token в один Promise.
// Простой in-memory Map<key, Promise>. Ключ живёт до тех пор, пока
// исходный promise не settle'нет — тогда автоматически удаляется.
// Это не рассчитано на межпроцессное шаринг (для этого есть Redis
// distributed lock), но нам достаточно per-instance — worst case
// 2 инстанса × 1 запрос = 2 upstream'а, а не 200.

const inflight = new Map();

/**
 * Run `factory()` under a keyed lock. Parallel callers with the same key
 * wait on the first invocation's promise.
 * @template T
 * @param {string} key
 * @param {() => Promise<T>} factory
 * @returns {Promise<T>}
 */
export function singleflight(key, factory) {
  const existing = inflight.get(key);
  if (existing) return existing;
  const p = (async () => {
    try { return await factory(); }
    finally { inflight.delete(key); }
  })();
  inflight.set(key, p);
  return p;
}

/**
 * Test-only: how many keys are inflight right now.
 */
export function inflightSize() {
  return inflight.size;
}
