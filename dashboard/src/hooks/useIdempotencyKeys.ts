import { useRef } from "react";
import { ApiError, newIdempotencyKey, type RequestOptions } from "@/lib/api";

/**
 * One Idempotency-Key per form submission. The same action with the same
 * payload reuses the pending key — so a double click or a retry after a
 * timeout is answered from the server's stored result instead of granting
 * twice. The key is dropped once the server gives a definitive answer
 * (success or a 4xx); a changed payload always gets a fresh key.
 */
export function useIdempotencyKeys() {
  const pending = useRef(new Map<string, { sig: string; key: string }>());
  return {
    opts(action: string, payload?: unknown): RequestOptions {
      const sig = JSON.stringify(payload ?? null);
      const cur = pending.current.get(action);
      if (cur && cur.sig === sig) return { idempotencyKey: cur.key };
      const key = newIdempotencyKey();
      pending.current.set(action, { sig, key });
      return { idempotencyKey: key };
    },
    settle(action: string, err?: unknown) {
      const definitive = !err || (err instanceof ApiError && err.status < 500);
      if (definitive) pending.current.delete(action);
    },
  };
}

