/**
 * Переключение темы.
 *
 * Обе темы — первого сорта (research §1.1, §3.3): выигрышная полярность
 * распределяется по людям индивидуально, а влияние выбора на время выполнения
 * задачи сопоставимо с влиянием выбора типа графика.
 *
 * По умолчанию — светлая (решение владельца). Не «системная»: у панели один
 * набор экранов и три администратора, и предсказуемость здесь дороже
 * автоматики. «Системная» осталась третьим значением, выбирается вручную —
 * кнопкой в шапке или командой в ⌘K.
 *
 * Механика простая: класс `dark` на <html>. Все цвета живут в CSS-переменных
 * (src/styles/tokens.css), поэтому смена класса меняет тему целиком, без
 * перерисовки React-дерева.
 */

export type Theme = "light" | "dark" | "system";

const KEY = "atlas.theme";

/** Кто хочет знать о смене темы (кнопка в шапке, палитра). */
const listeners = new Set<(t: Theme) => void>();

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light";
  } catch {
    return "light";
  }
}

function resolve(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme) {
  const dark = resolve(theme) === "dark";
  document.documentElement.classList.toggle("dark", dark);
  // Цвет системной панели в установленном приложении. Статических meta в
  // index.html недостаточно: они привязаны к prefers-color-scheme, а человек
  // мог выбрать тему вручную вопреки системе.
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]:not([media])');
  if (meta) meta.content = dark ? "#0C0E12" : "#F8F9FB";
}

export function setTheme(theme: Theme) {
  try {
    if (theme === "light") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    //
  }
  applyTheme(theme);
  listeners.forEach((fn) => fn(theme));
}

/** Возвращает отписку — удобно отдавать прямо из useEffect. */
export function subscribeTheme(fn: (t: Theme) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
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
