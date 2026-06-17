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
        // Светлая AURA-палитра: чистые белые поверхности, slate-tint
        // канвас, чёрно-графитовый текст. Все компоненты, использующие
        // существующие токены (bg / fg / border / accent), автоматически
        // перейдут в светлый стиль — править их не нужно.
        bg: {
          DEFAULT: "#F6F7F9",          // канвас
          subtle: "#EEF0F4",            // под-фон секций
          card: "#FFFFFF",              // карточка
          elevated: "#F1F3F6",          // лёгкая «пилюля» / квадрат с иконкой на белой карточке
        },
        border: {
          DEFAULT: "#E5E7EB",           // основной border (slate-200)
          subtle: "#F1F3F6",             // тонкий разделитель
        },
        fg: {
          DEFAULT: "#0B0F19",           // почти чёрный, чуть теплее pure black
          muted: "#475569",              // slate-600 для secondary текста
          subtle: "#94A3B8",             // slate-400 для подсказок / labels
        },
        // Primary accent — sky-500. Технологичный, спокойный, читается
        // на белом, не «электро»-неон. Чёрно-белый CTA — через
        // отдельные классы (bg-fg / text-bg) в компонентах.
        accent: {
          DEFAULT: "#0EA5E9",
          hover: "#0284C7",
          dark: "#0369A1",
        },
        secondary: {
          DEFAULT: "#06B6D4",            // cyan-500
          hover: "#0891B2",
        },
        success: "#10B981",              // emerald-500
        danger: "#EF4444",
        warning: "#F59E0B",
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
        // Soft layered shadows для светлой темы (slate-tint, не pure black).
        glow: "0 8px 28px -8px rgba(14,165,233,0.35)",
        "glow-sm": "0 4px 14px -4px rgba(14,165,233,0.25)",
        card: "0 1px 2px rgba(15,23,42,0.04), 0 4px 16px -8px rgba(15,23,42,0.06)",
        // Для чёрного CTA — мягкая «приподнятая» тень.
        cta: "0 8px 20px -8px rgba(11,15,25,0.45)",
      },
      animation: {
        "pulse-glow": "pulse-glow 2.5s ease-in-out infinite",
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
        // Auth success — кольцо расходится из центра.
        "ring-pulse": "ring-pulse 1.1s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        // Checkmark — рисуется как stroke-dasharray анимация.
        "check-draw": "check-draw 0.45s cubic-bezier(0.65, 0, 0.35, 1) forwards 0.15s",
        // Уход карточки + контента вверх с лёгким fade.
        "lift-out": "lift-out 0.5s cubic-bezier(0.7, 0, 0.3, 1) forwards 0.65s",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 0 0 rgba(14,165,233,0.5)",
          },
          "50%": {
            opacity: "0.8",
            boxShadow: "0 0 0 10px rgba(14,165,233,0)",
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
        "ring-pulse": {
          "0%": {
            transform: "scale(0.6)",
            opacity: "0.7",
            boxShadow: "0 0 0 0 rgba(34,197,94,0.4)",
          },
          "100%": {
            transform: "scale(1)",
            opacity: "1",
            boxShadow: "0 0 0 32px rgba(34,197,94,0)",
          },
        },
        "check-draw": {
          from: { strokeDashoffset: "24" },
          to: { strokeDashoffset: "0" },
        },
        "lift-out": {
          from: { opacity: "1", transform: "translateY(0)" },
          to: { opacity: "0", transform: "translateY(-12px)" },
        },
      },
    },
  },
  plugins: [],
};
