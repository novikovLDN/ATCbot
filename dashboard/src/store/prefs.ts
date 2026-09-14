/**
 * Operator preferences: theme, table density, motion.
 *
 * All three are written onto <html> as data attributes rather than
 * threaded through React: the stylesheet keys off them, so switching is a
 * style recalculation, not a re-render of every table row.
 */
import { create } from "zustand";

export type Density = "compact" | "comfortable" | "spacious";
export type MotionPref = "system" | "reduced";
export type Theme = "dark" | "light";

const DENSITY_KEY = "admin.density";
const MOTION_KEY = "admin.motion";
const THEME_KEY = "admin.theme";

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
  el.setAttribute("data-theme", theme);
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#CFCECA" : "#242423");
}

interface PrefsStore {
  density: Density;
  motion: MotionPref;
  theme: Theme;
  setDensity: (d: Density) => void;
  setMotion: (m: MotionPref) => void;
  setTheme: (t: Theme) => void;
}

const initialDensity = readStored<Density>(DENSITY_KEY, ["compact", "comfortable", "spacious"], "comfortable");
const initialMotion = readStored<MotionPref>(MOTION_KEY, ["system", "reduced"], "system");
// Dark (charcoal tiles on the light device) is the primary look.
const initialTheme = readStored<Theme>(THEME_KEY, ["dark", "light"], "dark");

apply(initialDensity, initialMotion, initialTheme);

export const usePrefs = create<PrefsStore>((set, get) => ({
  density: initialDensity,
  motion: initialMotion,
  theme: initialTheme,
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
    set({ theme });
  },
}));
