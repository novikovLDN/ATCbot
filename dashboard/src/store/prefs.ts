/**
 * Operator preferences: theme, table density, motion.
 *
 * All three are written onto <html> as data attributes rather than
 * threaded through React: the stylesheet keys off them, so switching is a
 * style recalculation, not a re-render of every table row.
 *
 * Theme: "system" (default) follows the iPhone's appearance live;
 * "light" / "dark" pin it. data-theme always holds the resolved value.
 */
import { create } from "zustand";

export type Density = "compact" | "comfortable" | "spacious";
export type MotionPref = "system" | "reduced";
export type Theme = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const DENSITY_KEY = "admin.density";
const MOTION_KEY = "admin.motion";
// v5: new key, so everyone starts on "system" — the v3 "dark" meant the
// old charcoal look, not a dark system appearance.
const THEME_KEY = "admin.theme.v5";

/** Grouped background of each theme — the status bar / theme-color. */
const THEME_COLOR: Record<ResolvedTheme, string> = { light: "#F2F2F7", dark: "#000000" };

const darkQuery: MediaQueryList | null =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

export function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme !== "system") return theme;
  return darkQuery?.matches ? "dark" : "light";
}

function readStored<T extends string>(key: string, allowed: T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    //
  }
}

function apply(density: Density, motion: MotionPref, theme: Theme) {
  const el = document.documentElement;
  if (density === "comfortable") el.removeAttribute("data-density");
  else el.setAttribute("data-density", density);
  if (motion === "reduced") el.setAttribute("data-motion", "reduced");
  else el.removeAttribute("data-motion");
  const resolved = resolveTheme(theme);
  el.setAttribute("data-theme", resolved);
  // index.html carries one theme-color per scheme. "system" keeps them
  // per scheme; a pinned theme sets both to its own background.
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
    const forDark = (m.getAttribute("media") ?? "").includes("dark");
    m.setAttribute("content", theme === "system" ? THEME_COLOR[forDark ? "dark" : "light"] : THEME_COLOR[resolved]);
  });
}

interface PrefsStore {
  density: Density;
  motion: MotionPref;
  theme: Theme;
  /** What is on screen now (system resolved). */
  resolved: ResolvedTheme;
  setDensity: (d: Density) => void;
  setMotion: (m: MotionPref) => void;
  setTheme: (t: Theme) => void;
}

const initialDensity = readStored<Density>(DENSITY_KEY, ["compact", "comfortable", "spacious"], "comfortable");
const initialMotion = readStored<MotionPref>(MOTION_KEY, ["system", "reduced"], "system");
const initialTheme = readStored<Theme>(THEME_KEY, ["system", "light", "dark"], "system");

apply(initialDensity, initialMotion, initialTheme);

export const usePrefs = create<PrefsStore>((set, get) => ({
  density: initialDensity,
  motion: initialMotion,
  theme: initialTheme,
  resolved: resolveTheme(initialTheme),
  setDensity: (density) => {
    persist(DENSITY_KEY, density);
    apply(density, get().motion, get().theme);
    set({ density });
  },
  setMotion: (motion) => {
    persist(MOTION_KEY, motion);
    apply(get().density, motion, get().theme);
    set({ motion });
  },
  setTheme: (theme) => {
    persist(THEME_KEY, theme);
    apply(get().density, get().motion, theme);
    set({ theme, resolved: resolveTheme(theme) });
  },
}));

// Follow the system switch (e.g. automatic dark mode at sunset) live.
darkQuery?.addEventListener?.("change", () => {
  const s = usePrefs.getState();
  if (s.theme !== "system") return;
  apply(s.density, s.motion, "system");
  usePrefs.setState({ resolved: resolveTheme("system") });
});
