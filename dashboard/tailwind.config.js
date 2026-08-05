/** @type {import('tailwindcss').Config} */

// Цвет берётся из CSS-переменных (src/styles/tokens.css), а не хардкодится
// здесь: значения переключаются классом .dark на <html>, конфиг Tailwind
// статичен и сам этого не умеет. Формат `rgb(var(--x) / <alpha-value>)` даёт
// работающий модификатор прозрачности — bg-success/12 и подобные (в коде их
// около полусотни) остаются рабочими.
const c = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    // Витрина примитивов существует только в dev-сборке (см. App.tsx), но
    // Tailwind сканирует исходники независимо от режима и тащил бы её классы
    // в продовый CSS. Исключаем — иначе платим килобайтами за страницу,
    // которой в проде нет.
    ...(process.env.NODE_ENV === "production" ? ["!./src/pages/UiKit.tsx"] : []),
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        // Inter вместо Urbanist: у Urbanist нет кириллического сабсета, из-за
        // чего русский текст рисовался фолбэком, а латинские подписи — самим
        // Urbanist, и в одной строке жили две гарнитуры (research §3.4).
        // Файлы локальные (public/fonts), @font-face — в tokens.css.
        sans: [
          "Inter Variable",
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        // Моноширинный — для ID, хешей, сумм в логах. У JetBrains Mono есть
        // кириллица и перечёркнутый ноль (§10.1).
        mono: [
          "JetBrains Mono Variable",
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },

      // Шкала из research §10.2 целиком. Базовый размер 14px, не 16: панель
      // плотная, на экране должно помещаться больше строк. Трекинг сжимается
      // с ростом кегля — модель Geist (§3.4).
      fontSize: {
        "2xs": ["11px", { lineHeight: "14px", letterSpacing: "0.02em" }],
        xs: ["12px", { lineHeight: "16px", letterSpacing: "0" }],
        sm: ["13px", { lineHeight: "18px", letterSpacing: "0" }],
        base: ["14px", { lineHeight: "20px", letterSpacing: "-0.006em" }],
        lg: ["16px", { lineHeight: "22px", letterSpacing: "-0.011em" }],
        xl: ["20px", { lineHeight: "26px", letterSpacing: "-0.014em" }],
        "2xl": ["24px", { lineHeight: "30px", letterSpacing: "-0.019em" }],
        "3xl": ["32px", { lineHeight: "36px", letterSpacing: "-0.021em" }],
        "4xl": ["44px", { lineHeight: "46px", letterSpacing: "-0.024em" }],
      },

      // Три начертания с ролями (§10.1): 400 читать, 500 интерактив и метки,
      // 600 заголовки и числа-герои. bold/extrabold специально сведены к 600 —
      // переменный Inter отрисует 700, но в системе такого веса нет, и
      // случайный font-bold не должен создавать четвёртый вес.
      fontWeight: {
        normal: "400",
        medium: "500",
        semibold: "600",
        bold: "600",
        extrabold: "600",
      },

      colors: {
        // --- Нейтральная шкала, 12 ступеней (модель Radix, §10.3).
        // Роли: 1–2 фон, 3 компонент, 4 hover, 5 pressed, 6 граница
        // неинтерактивного, 7 граница интерактивного, 8 focus-ring,
        // 9 заливка, 10 hover заливки, 11 текст, 12 текст высокого контраста.
        n: {
          1: c("--c-n1"),
          2: c("--c-n2"),
          3: c("--c-n3"),
          4: c("--c-n4"),
          5: c("--c-n5"),
          6: c("--c-n6"),
          7: c("--c-n7"),
          8: c("--c-n8"),
          9: c("--c-n9"),
          10: c("--c-n10"),
          11: c("--c-n11"),
          12: c("--c-n12"),
        },

        // --- Акцент, кобальт. DEFAULT = ступень 9 (заливка), hover = 10.
        // Имена DEFAULT/hover/dark сохранены ради существующих экранов:
        // bg-accent / hover:bg-accent-hover встречаются больше двухсот раз.
        accent: {
          1: c("--c-a1"),
          2: c("--c-a2"),
          3: c("--c-a3"),
          4: c("--c-a4"),
          5: c("--c-a5"),
          6: c("--c-a6"),
          7: c("--c-a7"),
          8: c("--c-a8"),
          9: c("--c-a9"),
          10: c("--c-a10"),
          11: c("--c-a11"),
          12: c("--c-a12"),
          DEFAULT: c("--c-a9"),
          hover: c("--c-a10"),
          text: c("--c-a11"),
          dark: c("--c-a12"),
        },

        // --- Поверхности. bg-bg — фон страницы, bg-bg-card — карточка.
        bg: {
          DEFAULT: c("--surface-page"),
          card: c("--surface-card"),
          subtle: c("--surface-subtle"),
          elevated: c("--surface-elevated"),
          overlay: c("--surface-overlay"),
        },
        border: {
          DEFAULT: c("--border-default"), // разделители, границы карточек
          subtle: c("--border-subtle"),
          strong: c("--border-strong"),
          // Граница поля ввода/чекбокса: по ней опознают компонент, поэтому
          // WCAG 1.4.11 требует 3:1 — обычная граница даёт 1.5:1.
          control: c("--border-control"),
        },
        fg: {
          DEFAULT: c("--fg-default"),
          muted: c("--fg-muted"),
          subtle: c("--fg-subtle"),
        },

        // --- Семантика: шесть состояний, не больше (§10.3). У каждого две
        // роли: DEFAULT — текст на светлом фоне (проверен на 4.5:1),
        // solid — сплошная заливка для точки, иконки, полосы.
        // Цвет никогда не единственный канал: рядом обязаны быть иконка и
        // слово (§4.11).
        success: { DEFAULT: c("--c-success-text"), solid: c("--c-success-solid") },
        warning: { DEFAULT: c("--c-warning-text"), solid: c("--c-warning-solid") },
        risk: { DEFAULT: c("--c-risk-text"), solid: c("--c-risk-solid") },
        danger: { DEFAULT: c("--c-danger-text"), solid: c("--c-danger-solid") },
        info: {
          DEFAULT: c("--c-info-text"),
          solid: c("--c-info-solid"),
          soft: c("--c-info-solid"), // устаревшее имя, осталось от прошлой палитры
        },
        // Деньги. Отдельные имена, потому что «пришло/ушло» в финансовой
        // таблице читается чаще, чем «успех/ошибка», и может разойтись с ними.
        money: { in: c("--c-money-in"), out: c("--c-money-out") },

        // --- УСТАРЕВШИЕ ПСЕВДОНИМЫ.
        // Исследование (§10.3) требует убрать tag-цвета и special: тринадцать
        // цветовых сущностей на шесть смысловых состояний. Удалить их прямо
        // сейчас — сломать 30 мест на экранах, которые переделываются на
        // следующем этапе. Поэтому имена оставлены, но указывают на
        // семантическую палитру: цветовой зоопарк уже исчез, а код собирается.
        // Удалять вместе с переделкой соответствующих экранов.
        special: c("--c-a11"),
        tagpurple: c("--c-a11"),
        tagblue: c("--c-info-text"),
        taggreen: c("--c-success-text"),
        tagamber: c("--c-warning-text"),
        tagrose: c("--c-danger-text"),
      },

      // Радиусы §10.4: sm 4 бейдж, md 6 кнопка и поле, lg 8 карточка,
      // xl 12 модалка. 2xl переопределён с 16 на 12, а 3xl с 24 на 16:
      // на экранах rounded-2xl стоит на каждой карточке, а крупный радиус
      // съедает горизонталь в плотной таблице.
      borderRadius: {
        sm: "4px",
        DEFAULT: "6px",
        md: "6px",
        lg: "8px",
        xl: "12px",
        "2xl": "12px",
        "3xl": "16px",
      },

      // Ровно три тени (§10.4): поповер, дропдаун, модалка. Карточка на
      // плоском фоне тени не имеет — только границу.
      // glow / glow-sm / card / cta / matte — старые имена, оставлены
      // указывающими на новые значения, чтобы не переписывать экраны.
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glow: "var(--shadow-md)",
        "glow-sm": "var(--shadow-sm)",
        card: "none",
        cta: "var(--shadow-sm)",
        matte: "var(--shadow-md)",
      },

      // Дробные значения прозрачности из старого конфига. Tailwind 3.4 не
      // умеет /12 и /15 без этого расширения, а таких мест в коде около
      // полусотни. Значения безобидные, оставлены до переделки экранов.
      opacity: {
        12: "0.12",
        15: "0.15",
        18: "0.18",
        35: "0.35",
      },

      // Высоты строк таблицы (§10.5). Собраны в токены, чтобы режим плотности
      // переключался в одном месте, а не подбирался паддингами на каждой
      // странице.
      height: {
        row: "40px", // комфортный режим
        "row-compact": "32px",
        "row-touch": "48px",
      },
      minHeight: {
        tap: "24px", // минимальная интерактивная цель, WCAG 2.2 SC 2.5.8
        "tap-touch": "44px", // практика iOS
      },
      minWidth: {
        tap: "24px",
        "tap-touch": "44px",
      },

      // Осталось семь анимаций из двадцати шести. Убраны все декоративные и
      // бесконечные: три aurora-blob по 16–22 с, glow-rotate 18 с,
      // spin-slow 14 с, ambient 20 с, ticker 32 с, sweep, tilt-hover,
      // bento-in, mount-card, route-in, lift-out, ring-pulse, check-draw,
      // pulse-glow, fade-up (research §3.6, §8.8). Бесконечная анимация на
      // фоне держит композитор занятым постоянно и не сообщает ничего.
      // Каждая оставшаяся отвечает на вопрос «что сейчас произошло»:
      animation: {
        "fade-in": "fade-in 0.2s ease-out", // появление блока
        "slide-up": "slide-up 0.24s cubic-bezier(0.16, 1, 0.3, 1)", // появление слоя снизу
        shimmer: "shimmer 1.6s linear infinite", // скелетон: «страница не зависла»
        "pulse-live": "pulse-live 1.8s ease-in-out infinite", // живое соединение
        "collapse-in": "collapse-in 0.2s cubic-bezier(0.16, 1, 0.3, 1)", // раскрытие секции
        "num-tick": "num-tick 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)", // счётчик обновился
        attention: "attention 0.9s cubic-bezier(0.4, 0, 0.2, 1) 1", // прилетело событие
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "pulse-live": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
        "collapse-in": {
          from: { opacity: "0", transform: "translateY(-4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "num-tick": {
          "0%": { transform: "translateY(0)", opacity: "1" },
          "40%": { transform: "translateY(-3px)", opacity: "0.85" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        attention: {
          "0%": { boxShadow: "0 0 0 0 rgb(var(--c-a9) / 0.35)" },
          "50%": { boxShadow: "0 0 0 6px rgb(var(--c-a9) / 0.12)" },
          "100%": { boxShadow: "0 0 0 0 rgb(var(--c-a9) / 0)" },
        },
      },
    },
  },
  plugins: [],
};
