/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        bg: {
          DEFAULT: "rgb(9 11 16)",
          subtle: "rgb(14 17 24)",
          card: "rgb(20 23 32)",
          elevated: "rgb(28 32 42)",
        },
        border: {
          DEFAULT: "rgb(38 42 54)",
          subtle: "rgb(28 32 42)",
        },
        fg: {
          DEFAULT: "rgb(237 240 245)",
          muted: "rgb(156 163 175)",
          subtle: "rgb(107 114 128)",
        },
        accent: {
          DEFAULT: "rgb(99 102 241)",
          hover: "rgb(129 140 248)",
        },
        success: "rgb(34 197 94)",
        danger: "rgb(239 68 68)",
        warning: "rgb(250 204 21)",
      },
      animation: {
        "pulse-glow": "pulse-glow 2.5s ease-in-out infinite",
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 0 rgba(99,102,241,0.4)" },
          "50%": { opacity: "0.8", boxShadow: "0 0 0 8px rgba(99,102,241,0)" },
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
