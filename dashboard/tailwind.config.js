/** @type {import('tailwindcss').Config} */

/* Dashboard v5 tokens (iOS grouped inset). Every colour is a CSS custom
   property holding an "R G B" triplet (index.css), so:
     - opacity modifiers keep working (bg-accent/15 → rgb(var(--c-accent) / .15));
     - the theme (system / light / dark) swaps by redefining the variables.
   The legacy names (tile, wall, bg, fg, border, accent, success …) are kept
   as aliases so every screen inherits the palette untouched. */
const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  // hover: variants only on devices that really hover — no sticky hover
  // states after a tap on the iPhone.
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      fontFamily: {
        // The system font: SF Pro on Apple devices, as in native apps.
        sans: ["-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Roboto", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "SF Mono", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        app: c("bg"),
        card: c("card"),
        elev: c("elev"),
        sep: c("sep"),
        wall: c("wall"),
        shell: c("shell"),
        tile: {
          1: c("tile-1"),
          2: c("tile-2"),
          3: c("tile-3"),
          4: c("tile-4"),
        },
        steel: c("steel"),
        fog: c("fog"),
        mist: c("mist"),
        ink: c("ink"),
        body: c("body"),
        mute: c("mute"),
        ash: c("ash"),
        onaccent: c("on-accent"),

        // Legacy aliases (older screens render inside a charcoal sheet).
        bg: {
          DEFAULT: c("tile-1"),
          subtle: c("tile-2"),
          card: c("tile-2"),
          elevated: c("tile-3"),
          high: c("tile-4"),
        },
        border: {
          DEFAULT: c("line"),
          subtle: c("line-soft"),
          strong: c("line-strong"),
        },
        fg: {
          DEFAULT: c("ink"),
          muted: c("body"),
          subtle: c("mute"),
          faint: c("ash"),
        },
        accent: {
          DEFAULT: c("accent"),
          hover: c("accent-hi"),
          dark: c("accent-lo"),
        },
        success: c("ok"),
        danger: c("err"),
        warning: c("warn"),
        info: { DEFAULT: c("info"), soft: c("info") },
        special: { DEFAULT: c("special"), soft: c("special") },
        tagpurple: c("special"),
        tagblue: c("info"),
        taggreen: c("ok"),
        tagamber: c("warn"),
        tagrose: c("err"),
        secondary: { DEFAULT: c("body"), hover: c("ink") },
      },
      borderRadius: {
        tile: "var(--r-tile)",
        shell: "var(--r-shell)",
        row: "var(--r-row)",
      },
      opacity: { 12: "0.12", 15: "0.15", 18: "0.18", 35: "0.35" },
      boxShadow: {
        card: "none",
        matte: "none",
        cta: "none",
        glow: "0 6px 22px -10px rgb(var(--c-accent) / 0.5)",
        "glow-sm": "0 4px 14px -8px rgb(var(--c-accent) / 0.45)",
        shell: "var(--shadow-shell)",
      },
      transitionTimingFunction: { ui: "cubic-bezier(0.2, 0, 0.2, 1)" },
      animation: {
        "fade-in": "fade-in 160ms cubic-bezier(0.2, 0, 0.2, 1)",
        "slide-up": "slide-up 200ms cubic-bezier(0.2, 0, 0.2, 1)",
        "fade-up": "fade-up 200ms cubic-bezier(0.2, 0, 0.2, 1) backwards",
        "route-in": "route-in 180ms cubic-bezier(0.2, 0, 0.2, 1)",
        "num-tick": "num-tick 200ms cubic-bezier(0.2, 0, 0.2, 1)",
        shimmer: "shimmer 1.6s linear infinite",
        "pulse-live": "pulse-live 2.2s ease-in-out infinite",
        "pulse-glow": "pulse-live 2.2s ease-in-out infinite",
        "ring-pulse": "ring-pulse 1.1s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "check-draw": "check-draw 0.45s cubic-bezier(0.65, 0, 0.35, 1) forwards 0.15s",
        "lift-out": "lift-out 0.5s cubic-bezier(0.7, 0, 0.3, 1) forwards 0.65s",
        "mount-card": "mount-card 0.5s cubic-bezier(0.16, 1, 0.3, 1) backwards",
        "collapse-in": "collapse-in 0.2s cubic-bezier(0.2, 0, 0.2, 1)",
        "tilt-hover": "fade-in 0.2s ease-out forwards",
        "bento-in": "fade-up 0.2s cubic-bezier(0.2, 0, 0.2, 1) backwards",
        attention: "pulse-live 0.9s ease-in-out 1",
        ambient: "pulse-live 20s ease-in-out infinite",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "route-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "num-tick": { "0%": { opacity: "1" }, "40%": { opacity: "0.55" }, "100%": { opacity: "1" } },
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        "pulse-live": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.45" } },
        "ring-pulse": {
          "0%": { transform: "scale(0.6)", opacity: "0.7" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        "check-draw": { from: { strokeDashoffset: "24" }, to: { strokeDashoffset: "0" } },
        "lift-out": {
          from: { opacity: "1", transform: "translateY(0)" },
          to: { opacity: "0", transform: "translateY(-12px)" },
        },
        "mount-card": {
          from: { opacity: "0", transform: "translateY(12px) scale(0.98)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "collapse-in": {
          from: { opacity: "0", transform: "translateY(-4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
