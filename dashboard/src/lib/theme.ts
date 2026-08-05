/**
 * Переключение темы.
 *
 * Обе темы — первого сорта (research §1.1, §3.3): выигрышная полярность
 * распределяется по людям индивидуально, а влияние выбора на время выполнения
 * задачи сопоставимо с влиянием выбора типа графика. Поэтому «system» —
 * значение по умолчанию, а не «light» с тёмной темой в довесок.
 *
 * Механика простая: класс `dark` на <html>. Все цвета живут в CSS-переменных
 * (src/styles/tokens.css), поэтому смена класса меняет тему целиком, без
 * перерисовки React-дерева.
 */

export type Theme = "light" | "dark" | "system";

const KEY = "atlas.theme";

export function getTheme(): Theme {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "system";
}

function resolve(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", resolve(theme) === "dark");
}

export function setTheme(theme: Theme) {
  if (theme === "system") localStorage.removeItem(KEY);
  else localStorage.setItem(KEY, theme);
  applyTheme(theme);
}

/** Вызывается один раз при старте, до первого рендера. */
export function initTheme() {
  applyTheme(getTheme());
  // Если тема системная — следим за системой: пользователь может переключить
  // её на ходу (расписание в macOS/iOS), и панель обязана поехать следом.
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (getTheme() === "system") applyTheme("system");
  });
}
