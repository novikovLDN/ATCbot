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
        // Тёмная FINPATH-палитра: глубокий канвас, дифференцированные
        // уровни поверхностей, off-white текст, lime-акцент. Цвета и
        // отметки HEX взяты из brand-deck (см. /docs).
        // Все компоненты на токенах (bg / fg / border / accent)
        // переходят в новый стиль автоматически.
        bg: {
          DEFAULT: "#0A0A0A",          // канвас — самый тёмный слой
          subtle: "#101010",            // под-фон секций
          card: "#161616",              // карточка
          elevated: "#212121",          // приподнятая поверхность (пилюли, активные tab'ы, чипы)
        },
        border: {
          DEFAULT: "#262626",           // основной border на dark
          subtle: "#1C1C1C",             // тонкий разделитель
        },
        fg: {
          DEFAULT: "#FCFCFC",           // off-white — основной текст
          muted: "#A1A1AA",              // zinc-400 для secondary
          subtle: "#71717A",             // zinc-500 для labels / подсказок
        },
        // Primary accent — матовый platinum-белый. Дашборд специально
        // монохромный: премиальный, технологичный, без лишних цветов.
        // Бот-фронт (Telegram-приложение) сохраняет лаймовую айдентику.
        // На тёмном фоне #F5F5F5 читается как «hi-tech tool».
        accent: {
          DEFAULT: "#F5F5F5",
          hover: "#FFFFFF",
          dark: "#D4D4D8",
        },
        secondary: {
          DEFAULT: "#9CA3AF",            // cool platinum-grey для secondary
          hover: "#D1D5DB",
        },
        success: "#86EFAC",              // мятный — +% и положительные дельты
        danger: "#FF6B6B",               // мягкий красный, не выжигает на dark
        warning: "#FFD66B",              // тёплый янтарь
        // Сдержанные технологичные акценты. Используем ТОЧЕЧНО для
        // семантической навигации по цвету:
        //   info    — real-time индикаторы, ссылки, hi-tech chip'ы
        //   special — VIP-статусы, «эксклюзивно» / premium-подписки
        info: {
          DEFAULT: "#7DD3FC",             // sky-300, чуть охлаждённый
          soft: "#38BDF8",
        },
        special: {
          DEFAULT: "#C4B5FD",             // violet-300, приглушённый
          soft: "#A78BFA",
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
        // Дашборд-glow: мягкое белое свечение вокруг активных элементов.
        // Матовое, не насыщенное — просто «premium wow moment».
        glow: "0 8px 28px -8px rgba(245,245,245,0.35)",
        "glow-sm": "0 4px 14px -4px rgba(245,245,245,0.22)",
        // .card — внутренний тонкий inset сверху + тёплый deep drop
        // снизу. Тонкая работа с уровнем поверхностей — за счёт этого
        // карточка ощущается «поднятой».
        card: "0 1px 0 rgba(255,255,255,0.05) inset, 0 12px 32px -18px rgba(0,0,0,0.6)",
        // CTA shadow на dark canvas — глубже, матово-белый inset.
        cta: "0 10px 24px -10px rgba(0,0,0,0.7), 0 0 0 1px rgba(245,245,245,0.14) inset",
        // Matte-glass: subtle ring для стеклянных панелей.
        matte: "0 1px 0 rgba(255,255,255,0.06) inset, 0 0 0 1px rgba(255,255,255,0.02)",
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
