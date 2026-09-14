// Token storage. Simple localStorage wrapper — we never need refresh
// tokens because the admin can re-issue via /admin in the bot any time.

const KEY = "atlas.admin.token";

export const auth = {
  get(): string | null {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  set(token: string) {
    try {
      localStorage.setItem(KEY, token);
    } catch {
      // ignore (private mode, quota, etc.)
    }
  },
  clear() {
    try {
      localStorage.removeItem(KEY);
    } catch {
      //
    }
  },
};

// Pull `?login=<jwt>` out of the URL on first paint and stash it in
// localStorage. Called by main.tsx before React mounts so the
// auth-gate sees the token immediately.
export function captureMagicLink(): boolean {
  const u = new URL(window.location.href);
  const login = u.searchParams.get("login");
  if (!login) return false;
  auth.set(login);
  u.searchParams.delete("login");
  window.history.replaceState({}, "", u.pathname + (u.search ? u.search : "") + u.hash);
  return true;
}
