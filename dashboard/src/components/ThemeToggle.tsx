import { useEffect, useState } from "react";
import { Sun, Moon, MonitorSmartphone } from "lucide-react";
import { getTheme, setTheme, subscribeTheme, type Theme } from "@/lib/theme";

/**
 * Переключатель темы в шапке.
 *
 * Переключателя не было в интерфейсе вообще: обе темы существовали в токенах,
 * но переключить их можно было только на витрине примитивов, которой в проде
 * нет. Здесь одна кнопка по кругу светлая → тёмная → системная. Кругом, а не
 * тремя кнопками, потому что в шапке дорога каждая сотня пикселей, а выбор
 * из трёх значений всё равно продублирован в палитре ⌘K отдельными командами.
 *
 * Подпись читается вслух целиком: скринридер получит и текущее состояние, и
 * что произойдёт по нажатию.
 */

const NEXT: Record<Theme, Theme> = { light: "dark", dark: "system", system: "light" };

const LABEL: Record<Theme, string> = {
  light: "светлая",
  dark: "тёмная",
  system: "как в системе",
};

export function ThemeToggle() {
  const [theme, setLocal] = useState<Theme>(getTheme);

  // Тему меняет ещё и палитра. Без подписки кнопка показывала бы старое
  // значение до следующей перерисовки.
  useEffect(() => subscribeTheme(setLocal), []);

  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : MonitorSmartphone;

  return (
    <button
      type="button"
      className="icon-btn icon-btn-sm"
      title={`Тема: ${LABEL[theme]}`}
      aria-label={`Тема: ${LABEL[theme]}. Переключить на «${LABEL[NEXT[theme]]}»`}
      onClick={() => setTheme(NEXT[theme])}
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}
