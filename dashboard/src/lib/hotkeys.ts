/**
 * Глобальные горячие клавиши оболочки.
 *
 * Что здесь и почему (ux-patterns §1.1, §1.5):
 *
 *  ⌘K / Ctrl+K       — палитра. Один шорткат везде, включая поля ввода:
 *                      Superhuman ставит это первым принципом.
 *  ⌘⌥K / Ctrl+Alt+K  — то же самое второй комбинацией. Так делает GitHub на
 *                      случай, когда ⌘K уже занят редактором в поле ввода
 *                      (в Markdown-полях это «вставить ссылку»).
 *  g, затем буква    — переход в раздел. Первая клавиша одиночная, а это
 *                      WCAG 2.1.4 (уровень A): такой шорткат обязан либо
 *                      выключаться, либо работать только при фокусе на
 *                      компоненте. Выполнены оба смягчения сразу:
 *                        — в поле ввода аккорд не срабатывает вовсе;
 *                        — есть тумблер (палитра, команда «Однобуквенные
 *                          шорткаты»), состояние живёт в localStorage.
 *                      На опасных действиях однобуквенных шорткатов нет и не
 *                      будет: аккорды навешаны только на навигацию, которая
 *                      ничего не меняет.
 */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { NAV } from "./nav";

const KEY = "atlas.hotkeys";

/** Одиночные клавиши включены, пока их явно не выключили. */
export function singleKeyHotkeysEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSingleKeyHotkeys(on: boolean) {
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "off");
  } catch {
    //
  }
}

/** Курсор в поле ввода — значит буквы принадлежат тексту, а не интерфейсу. */
export function isEditable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return (
    tag === "input" ||
    tag === "textarea" ||
    tag === "select" ||
    el.isContentEditable === true
  );
}

/** ⌘K, Ctrl+K, ⌘⌥K, Ctrl+Alt+K — всё это «открыть палитру». */
export function isPaletteKey(e: KeyboardEvent): boolean {
  // e.code, а не e.key: при русской раскладке e.key для этой клавиши — «л»,
  // и сравнение по букве перестало бы работать ровно там, где панель живёт.
  const isK = e.code === "KeyK" || e.key === "k" || e.key === "K" || e.key === "л" || e.key === "Л";
  return isK && (e.metaKey || e.ctrlKey);
}

/** Сколько ждём вторую клавишу аккорда, прежде чем забыть про g. */
const CHORD_MS = 1500;

/**
 * Ставит слушатель на документ. Возвращать ничего не нужно: раскладку
 * разбираем по e.code, поэтому «g» работает и в русской раскладке (клавиша
 * «п»).
 */
export function useShellHotkeys(openPalette: () => void) {
  const navigate = useNavigate();
  // Ждём вторую клавишу аккорда. Ref, а не state: перерисовка тут не нужна,
  // а лишний рендер на каждое нажатие клавиши — заметная цена.
  const chordUntil = useRef(0);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isPaletteKey(e)) {
        e.preventDefault();
        openPalette();
        return;
      }

      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isEditable(e.target)) return;
      if (!singleKeyHotkeysEnabled()) return;

      const now = Date.now();
      if (now < chordUntil.current) {
        chordUntil.current = 0;
        const letter = e.code.startsWith("Key") ? e.code.slice(3).toLowerCase() : "";
        const target = NAV.find((s) => s.chord === letter);
        if (target) {
          e.preventDefault();
          navigate(target.to);
        }
        return;
      }

      if (e.code === "KeyG") {
        chordUntil.current = now + CHORD_MS;
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navigate, openPalette]);
}
