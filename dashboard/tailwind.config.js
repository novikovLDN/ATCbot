/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          // Geist (Vercel) — техно-sans с кириллицей.
          // Fustat / Inter — fallback на случай блокировки Google Fonts.
          "Geist",
          "Fustat",
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "Geist Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Deep black canvas, near-black surfaces. The reference uses
        // pure #090909 background with extremely subtle layering;
        // surfaces only step up by ~5-8 lightness.
        bg: {
          DEFAULT: "rgb(9 9 9)",
          subtle: "rgb(14 14 14)",
          card: "rgb(20 20 22)",
          elevated: "rgb(28 28 32)",
        },
        border: {
          DEFAULT: "rgb(38 38 42)",
          subtle: "rgb(28 28 32)",
        },
        fg: {
          DEFAULT: "rgb(245 245 247)",
          muted: "rgb(160 160 165)",
          subtle: "rgb(110 110 115)",
        },
        // Lime green primary — bright, almost neon. Pairs with dark
        // text (text-bg) for legibility on filled buttons.
        accent: {
          DEFAULT: "#ABF43F",
          hover: "#BDF55F",
          dark: "#7CCC10",
        },
        // Cyan secondary for ai/info accents.
        secondary: {
          DEFAULT: "#3FF4E5",
          hover: "#5DF7EA",
        },
        success: "#22C55E",
        danger: "#EF4444",
        warning: "#FACC15",
        // Category tag tints — used by tag-* badges so different
        // segments get a distinct vibe without screaming.
        tagpurple: "#A855F7",
        tagblue: "#3B82F6",
        taggreen: "#22C55E",
        tagamber: "#F59E0B",
        tagrose: "#F43F5E",
      },
      opacity: {
        "12": "0.12",
        "15": "0.15",
        "18": "0.18",
        "35": "0.35",
      },
      boxShadow: {
        glow: "0 4px 24px -2px rgba(171,244,63,0.45)",
        "glow-sm": "0 2px 12px -2px rgba(171,244,63,0.35)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
      },
      animation: {
        "pulse-glow": "pulse-glow 2.5s ease-in-out infinite",
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 0 0 rgba(171,244,63,0.5)",
          },
          "50%": {
            opacity: "0.8",
            boxShadow: "0 0 0 10px rgba(171,244,63,0)",
          },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
