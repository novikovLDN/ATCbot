/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          // Urbanist — гео-sans из референса (футуристично, читаемо,
          // кириллица из коробки). Geist / Inter — fallback на случай
          // блокировки Google Fonts.
          "Urbanist",
          "Geist",
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
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
        // WHITE THEME 2026/2027 — мягкий off-white канвас, тонкие
        // grey-разделители, deep-slate текст, colored accents для
        // семантики. Тренд: light + noise-texture + subtle depth
        // (без drop-shadow'ов, только hair-line borders + inset
        // highlight). Дизайн mobile-first: все размеры комфортны
        // для тача (min 40px), viewport-safe padding через env().
        bg: {
          DEFAULT: "#FBFBF9",           // канвас — тёплый off-white (не чистый белый — не бьёт по глазам на OLED)
          subtle: "#F5F5F2",            // под-фон секций
          card: "#FFFFFF",              // карточка — pure white для контраста
          elevated: "#EEEEEA",          // pill'ы, активные tab'ы, чипы
        },
        border: {
          DEFAULT: "#E5E5E0",           // основной hair-line border
          subtle: "#EEEEEA",            // ещё тоньше — divider между row'ами
        },
        fg: {
          DEFAULT: "#0F1720",           // deep-slate — основной текст (не чёрный — мягче)
          muted: "#4B5563",             // slate-600 для secondary
          subtle: "#6B7280",            // slate-500 для labels / подсказок
        },
        // Primary accent — deep-slate (почти чёрный). Ткни «Купить» —
        // видит контрастный CTA. В light-теме тёмный accent работает
        // сильнее любого цвета для главного действия.
        accent: {
          DEFAULT: "#0F1720",           // deep-slate, contrast on white
          hover: "#1F2937",
          dark: "#000000",
        },
        secondary: {
          DEFAULT: "#6B7280",
          hover: "#4B5563",
        },
        success: "#10B981",              // emerald-500 — сочный, но не крикливый
        danger: "#EF4444",               // red-500
        warning: "#F59E0B",              // amber-500
        // Точечные акценты:
        //   info    — real-time индикаторы, ссылки на детали
        //   special — VIP / premium статусы
        info: {
          DEFAULT: "#2563EB",             // blue-600
          soft: "#3B82F6",
        },
        special: {
          DEFAULT: "#7C3AED",             // violet-600, для VIP
          soft: "#8B5CF6",
        },
        // Category tag tints — desaturated так, чтобы не конкурировать
        // с лаймовым акцентом, но различимы между собой.
        tagpurple: "#B794F4",
        tagblue: "#7AB8FF",
        taggreen: "#A6FFB3",
        tagamber: "#FFD66B",
        tagrose: "#FF9DAE",
      },
      opacity: {
        "12": "0.12",
        "15": "0.15",
        "18": "0.18",
        "35": "0.35",
      },
      boxShadow: {
        // WHITE THEME shadows — тонкие, warm, никакого drop-shadow'а.
        // Приоритет: hair-line ring + inset highlight. Тренд 2026:
        // «flat with depth cues».
        glow: "0 8px 24px -12px rgba(15,23,32,0.12)",
        "glow-sm": "0 4px 12px -6px rgba(15,23,32,0.08)",
        // .card на white canvas — ring + минимальный soft drop.
        // Inset-highlight снизу даёт «air layer» между картой и фоном.
        card: "0 0 0 1px rgba(15,23,32,0.05), 0 1px 2px rgba(15,23,32,0.03), 0 4px 12px -6px rgba(15,23,32,0.05)",
        // CTA (dark button on white): soft-lift + inner-highlight.
        cta: "0 4px 14px -4px rgba(15,23,32,0.35), 0 0 0 1px rgba(0,0,0,0.06) inset",
        // Frosted-glass panel — для sticky top-bar / modals над контентом.
        matte: "0 0 0 1px rgba(15,23,32,0.06), 0 8px 24px -12px rgba(15,23,32,0.12)",
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
        // Скелетоны с движущимся бликом (shimmer-bg).
        "shimmer": "shimmer 1.6s linear infinite",
        // Медленный gradient-rotation для hero-фонового glow.
        "glow-rotate": "glow-rotate 18s linear infinite",
        // Появление секций — fade + slight rise. Используется со
        // styled animation-delay для stagger-эффекта.
        "fade-up": "fade-up 0.55s cubic-bezier(0.16, 1, 0.3, 1) backwards",
        // Login card mount — soft scale + rise.
        "mount-card": "mount-card 0.7s cubic-bezier(0.16, 1, 0.3, 1) backwards",
        // Floating aurora blobs — длинные drift анимации с разным offset.
        "blob-slow":   "blob-slow   16s ease-in-out infinite",
        "blob-slow-2": "blob-slow-2 19s ease-in-out infinite",
        "blob-slow-3": "blob-slow-3 22s ease-in-out infinite",
        // Живой pulse-live для «real-time» точки: коротко и мягко.
        "pulse-live":  "pulse-live 1.8s ease-in-out infinite",
        // Rotating conic для tech-border-anim — держит edge card'а в живом
        // состоянии. Медленно, ~14 сек — не отвлекает.
        "spin-slow":   "spin-slow 14s linear infinite",
        // Sweep-shine — блик по card'у при hover.
        "sweep":       "sweep 1.2s cubic-bezier(0.16, 1, 0.3, 1)",
        // Route-transition wrapper: soft-slide-fade.
        "route-in":    "route-in 0.45s cubic-bezier(0.16, 1, 0.3, 1)",
        // Number tick — маленький bump при обновлении счётчика.
        "num-tick":    "num-tick 0.35s cubic-bezier(0.34, 1.56, 0.64, 1)",
        // Ticker-marquee для лента-виджета.
        "ticker":      "ticker 32s linear infinite",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 0 0 rgba(215,255,103,0.55)",
          },
          "50%": {
            opacity: "0.8",
            boxShadow: "0 0 0 10px rgba(215,255,103,0)",
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
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "glow-rotate": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "mount-card": {
          from: { opacity: "0", transform: "translateY(16px) scale(0.96)" },
          to:   { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "blob-slow": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%":      { transform: "translate(40px, -30px) scale(1.1)" },
          "66%":      { transform: "translate(-20px, 40px) scale(0.95)" },
        },
        "blob-slow-2": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%":      { transform: "translate(-50px, 30px) scale(1.1)" },
          "66%":      { transform: "translate(30px, -20px) scale(0.92)" },
        },
        "blob-slow-3": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%":      { transform: "translate(35px, -25px) scale(1.08)" },
        },
        "pulse-live": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 0 0 rgba(125,211,252,0.55)",
          },
          "50%": {
            opacity: "0.7",
            boxShadow: "0 0 0 8px rgba(125,211,252,0)",
          },
        },
        "spin-slow": {
          "0%":   { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        "sweep": {
          from: { transform: "translateX(-120%)" },
          to:   { transform: "translateX(120%)" },
        },
        "route-in": {
          from: { opacity: "0", transform: "translateY(8px) scale(0.99)" },
          to:   { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "num-tick": {
          "0%":   { transform: "translateY(0)",  opacity: "1" },
          "40%":  { transform: "translateY(-4px)", opacity: "0.85" },
          "100%": { transform: "translateY(0)",  opacity: "1" },
        },
        "ticker": {
          from: { transform: "translateX(0%)" },
          to:   { transform: "translateX(-50%)" },
        },
      },
    },
  },
  plugins: [],
};
