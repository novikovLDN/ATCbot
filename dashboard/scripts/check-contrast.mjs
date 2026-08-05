#!/usr/bin/env node
/**
 * Проверка контраста токенов по WCAG 2.1 (формула относительной яркости 1.4.3).
 *
 * Зачем скрипт, а не глаз: исследование §10 прямо требует проверять контраст
 * инструментом, а Stripe в своём кейсе вывел контраст в правило и проверяет
 * его автоматически (§4.1). Пары ниже — это те сочетания, которые реально
 * встречаются в интерфейсе; если добавили новый токен и красите им текст,
 * добавьте пару сюда, иначе проверки на него нет.
 *
 * Пороги: 4.5:1 обычный текст, 3:1 крупный (≥24px или ≥19px полужирный) и
 * нетекстовые элементы — границы, focus-ring, иконки-статусы.
 *
 * Запуск:  node scripts/check-contrast.mjs [--md]
 * Код возврата 1, если хоть одна пара не добрала свой порог.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const TOKENS = resolve(here, "../src/styles/tokens.css");

/* --- разбор tokens.css --------------------------------------------------
   Достаём два блока — :root (светлая) и .dark (тёмная). Значение либо тройка
   «R G B», либо ссылка var(--другой-токен); ссылки резолвим рекурсивно.     */
function parseBlock(css, selector) {
  const start = css.indexOf(selector + " {");
  if (start === -1) throw new Error(`не нашёл блок ${selector} в tokens.css`);
  const from = css.indexOf("{", start) + 1;
  const to = css.indexOf("\n}", from);
  const body = css.slice(from, to);
  const out = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    out[m[1]] = m[2].trim();
  }
  return out;
}

function resolveVar(name, scope, base, seen = new Set()) {
  if (seen.has(name)) throw new Error(`циклическая ссылка на ${name}`);
  seen.add(name);
  const raw = scope[name] ?? base[name];
  if (raw === undefined) throw new Error(`токен ${name} не найден`);
  const ref = raw.match(/^var\((--[\w-]+)\)$/);
  if (ref) return resolveVar(ref[1], scope, base, seen);
  const rgb = raw.match(/^(\d+)\s+(\d+)\s+(\d+)$/);
  if (!rgb) throw new Error(`токен ${name} = "${raw}" — не тройка RGB`);
  return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
}

/* --- контраст ----------------------------------------------------------- */
const channel = (v) => {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const l1 = luminance(a);
  const l2 = luminance(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};
const hex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0").toUpperCase()).join("");

/* --- какие пары проверяем ------------------------------------------------
   min: 4.5 — обычный текст; 3 — крупный текст, границы, focus-ring, точки
   статуса. Белый в светлой теме на заливке — это текст кнопки/бейджа.       */
const WHITE = "#FFFFFF";
const PAIRS = [
  // Текст на поверхностях
  ["Основной текст", "--fg-default", "--surface-page", 4.5],
  ["Основной текст", "--fg-default", "--surface-card", 4.5],
  ["Основной текст", "--fg-default", "--surface-subtle", 4.5],
  ["Вторичный текст", "--fg-muted", "--surface-page", 4.5],
  ["Вторичный текст", "--fg-muted", "--surface-card", 4.5],
  ["Вторичный текст", "--fg-muted", "--surface-elevated", 4.5],
  ["Подписи", "--fg-subtle", "--surface-page", 4.5],
  ["Подписи", "--fg-subtle", "--surface-card", 4.5],
  ["Подписи", "--fg-subtle", "--surface-subtle", 4.5],
  // Акцент
  ["Ссылка/акцентный текст", "--c-a11", "--surface-card", 4.5],
  ["Ссылка/акцентный текст", "--c-a11", "--surface-page", 4.5],
  ["Акцентный текст на подложке a3", "--c-a11", "--c-a3", 4.5],
  ["Текст кнопки на заливке a9", WHITE, "--c-a9", 4.5],
  ["Текст кнопки на hover a10", WHITE, "--c-a10", 4.5],
  ["Focus-ring к фону страницы", "--c-a8", "--surface-page", 3],
  ["Focus-ring к карточке", "--c-a8", "--surface-card", 3],
  ["Focus-ring к плотной поверхности", "--c-a8", "--surface-subtle", 3],
  // Границы. 3:1 по WCAG 1.4.11 требуется только там, где по границе опознают
  // сам компонент — поле ввода, чекбокс, кнопка. Разделители строк таблицы
  // (--border-default/--border-subtle) под критерий не подпадают и намеренно
  // остаются слабыми, иначе плотная таблица превращается в решётку.
  ["Граница поля ввода к карточке", "--border-control", "--surface-card", 3],
  ["Граница поля ввода к фону страницы", "--border-control", "--surface-page", 3],
  // Семантика: текст на светлых/тёмных поверхностях
  ["Успех — текст", "--c-success-text", "--surface-card", 4.5],
  ["Успех — текст на фоне страницы", "--c-success-text", "--surface-page", 4.5],
  ["Ожидание — текст", "--c-warning-text", "--surface-card", 4.5],
  ["Ожидание — текст на фоне страницы", "--c-warning-text", "--surface-page", 4.5],
  ["Риск — текст", "--c-risk-text", "--surface-card", 4.5],
  ["Риск — текст на фоне страницы", "--c-risk-text", "--surface-page", 4.5],
  ["Отказ — текст", "--c-danger-text", "--surface-card", 4.5],
  ["Отказ — текст на фоне страницы", "--c-danger-text", "--surface-page", 4.5],
  ["Информация — текст", "--c-info-text", "--surface-card", 4.5],
  ["Деньги + — текст", "--c-money-in", "--surface-card", 4.5],
  ["Деньги − — текст", "--c-money-out", "--surface-card", 4.5],
  // Семантика: точка/значок статуса на поверхности — нетекстовый порог 3:1
  ["Успех — заливка как значок", "--c-success-solid", "--surface-card", 3],
  ["Ожидание — заливка как значок", "--c-warning-solid", "--surface-card", 3],
  ["Риск — заливка как значок", "--c-risk-solid", "--surface-card", 3],
  ["Отказ — заливка как значок", "--c-danger-solid", "--surface-card", 3],
];

// Сплошной семантический бейдж: класс «bg-<состояние> text-bg-card».
// Фоном идёт токен --c-*-text, а не --c-*-solid: заливочная ступень с текстом
// поверх даёт 3.7–4.4:1 и порог не проходит — она предназначена для точек,
// иконок и полос, а не для подложки под мелкий текст.
// Цветом текста берётся поверхность карточки: в светлой теме она почти белая,
// в тёмной почти чёрная, и пара переворачивается вместе с темой сама.
const ON_SOLID = [
  ["Успех", "--c-success-text"],
  ["Ожидание", "--c-warning-text"],
  ["Риск", "--c-risk-text"],
  ["Отказ", "--c-danger-text"],
  ["Информация", "--c-info-text"],
  ["Нейтральное", "--c-n12"],
];

function run() {
  const css = readFileSync(TOKENS, "utf8");
  const light = parseBlock(css, ":root");
  const dark = parseBlock(css, ".dark");
  const themes = [
    ["светлая", light, light],
    ["тёмная", dark, light],
  ];

  const rows = [];
  let failed = 0;

  const val = (token, scope, base) =>
    token.startsWith("#")
      ? [1, 3, 5].map((i) => parseInt(token.slice(i, i + 2), 16))
      : resolveVar(token, scope, base);

  for (const [themeName, scope, base] of themes) {
    for (const [label, fg, bg, min] of PAIRS) {
      const a = val(fg, scope, base);
      const b = val(bg, scope, base);
      const ratio = contrast(a, b);
      const ok = ratio + 1e-9 >= min;
      if (!ok) failed++;
      rows.push({
        theme: themeName,
        label,
        fg: `${fg} ${hex(a)}`,
        bg: `${bg} ${hex(b)}`,
        ratio,
        min,
        ok,
      });
    }
    // Текст на сплошном семантическом бейдже.
    for (const [label, bgToken] of ON_SOLID) {
      const bg = val(bgToken, scope, base);
      const text = val("--surface-card", scope, base);
      const ratio = contrast(text, bg);
      const ok = ratio >= 4.5;
      if (!ok) failed++;
      rows.push({
        theme: themeName,
        label: `${label} — сплошной бейдж`,
        fg: `--surface-card ${hex(text)}`,
        bg: `${bgToken} ${hex(bg)}`,
        ratio,
        min: 4.5,
        ok,
      });
    }
  }

  const md = process.argv.includes("--md");
  if (md) {
    console.log("| Тема | Пара | Текст | Фон | Контраст | Порог | Итог |");
    console.log("|---|---|---|---|---|---|---|");
    for (const r of rows) {
      console.log(
        `| ${r.theme} | ${r.label} | ${r.fg} | ${r.bg} | ${r.ratio.toFixed(2)}:1 | ${r.min}:1 | ${r.ok ? "ок" : "**мало**"} |`,
      );
    }
  } else {
    for (const r of rows) {
      const mark = r.ok ? "ok  " : "FAIL";
      console.log(
        `${mark} [${r.theme}] ${r.label.padEnd(34)} ${r.ratio.toFixed(2).padStart(6)}:1 (нужно ${r.min}) ${r.fg} на ${r.bg}`,
      );
    }
  }
  console.log(`\nПроверено пар: ${rows.length}, не добрали порог: ${failed}`);
  process.exit(failed === 0 ? 0 : 1);
}

run();
