/**
 * Реестр команд палитры ⌘K.
 *
 * Принципы, по которым он собран (ux-patterns §1.1, Superhuman):
 *
 *  — «Централизация»: палитра — единственное место, где лежат все команды.
 *    Поэтому здесь и навигация по всем девяти разделам, и вкладки внутри них,
 *    и действия. Не два меню (⌘K и ⌘P), а одно.
 *  — «Дешёвое добавление»: чтобы добавить команду, надо дописать один объект
 *    в массив ниже. Ничего регистрировать больше нигде не нужно.
 *  — «Контекст поднимает, а не прячет»: у команды есть `section`. Совпал с
 *    текущим путём — множитель растёт. Ни одна команда при этом не исчезает,
 *    потому что «спрятать можно только полностью нерелевантное».
 *  — «Шорткат рядом с командой»: поле `shortcut` рисуется справа от пункта.
 *    Это и есть механизм обучения — новичок его не замечает, опытный
 *    запоминает от повторного показа (NN/g, §1.4).
 *
 * Опасных однобуквенных шорткатов здесь нет: аккорды «g …» висят только на
 * переходах, которые ничего не меняют (WCAG 2.1.4).
 */
import {
  Megaphone,
  RefreshCw,
  Sun,
  Moon,
  MonitorSmartphone,
  Keyboard,
  LogOut,
  type LucideIcon,
} from "lucide-react";
import { NAV } from "./nav";
import { singleKeyHotkeysEnabled, setSingleKeyHotkeys } from "./hotkeys";
import { setTheme, getTheme } from "./theme";

export interface Command {
  /** Уникальный и стабильный: по нему cmdk хранит выделение. Только латиница
   *  в нижнем регистре — cmdk нормализует значения. */
  id: string;
  title: string;
  /** Правее названия серым: куда ведёт или что уточняет. */
  hint?: string;
  icon: LucideIcon;
  kind: "nav" | "action";
  /** Синонимы для нечёткого поиска: «юзеры» вместо «пользователи» и т.п. */
  keywords?: string[];
  /** Клавиши, как они рисуются: ["G", "U"]. */
  shortcut?: string[];
  /** Путь, при нахождении на котором команда становится уместнее. */
  section?: string;
  /** Базовый вес. Ежедневное тяжелее редкого. */
  weight?: number;
  run: () => void;
}

export interface CommandCtx {
  navigate: (to: string) => void;
  /** Сбросить кэш запросов — «перечитать всё с сервера». */
  refetchAll: () => void;
  logout: () => void;
  notify: (text: string) => void;
}

export function buildCommands(ctx: CommandCtx): Command[] {
  const cmds: Command[] = [];

  // ── Навигация: девять разделов и все вкладки внутри них ──────────────
  for (const s of NAV) {
    const daily = s.group === "daily";
    cmds.push({
      id: `nav.${s.to}`,
      title: s.label,
      icon: s.icon,
      kind: "nav",
      keywords: s.keywords,
      shortcut: ["G", s.chord.toUpperCase()],
      section: s.to,
      weight: daily ? 1.2 : s.group === "regular" ? 1 : 0.9,
      run: () => ctx.navigate(s.to),
    });

    // Вкладки. Раньше это были отдельные пункты меню — из сайдбара они ушли,
    // но из палитры не должны: искать «промокоды» человек будет по слову
    // «промокоды», а не по слову «монетизация».
    for (const t of s.tabs ?? []) {
      if (t.to === s.to) continue; // первая вкладка = сам раздел, уже добавлен
      cmds.push({
        id: `nav.${t.to}`,
        title: t.label,
        hint: s.label,
        icon: s.icon,
        kind: "nav",
        keywords: s.keywords,
        section: s.to,
        weight: daily ? 1 : 0.85,
        run: () => ctx.navigate(t.to),
      });
    }
  }

  // ── Действия (режим «>») ────────────────────────────────────────────
  cmds.push({
    id: "action.broadcast.new",
    title: "Создать рассылку",
    icon: Megaphone,
    kind: "action",
    keywords: ["сообщение", "отправить", "новая"],
    section: "/broadcasts",
    weight: 1.1,
    run: () => ctx.navigate("/broadcasts/new"),
  });

  cmds.push({
    id: "action.refetch",
    title: "Перечитать данные",
    hint: "сбросить кэш запросов",
    icon: RefreshCw,
    kind: "action",
    keywords: ["обновить", "refresh", "перезагрузить"],
    weight: 1,
    run: () => {
      ctx.refetchAll();
      ctx.notify("Данные перечитываются с сервера");
    },
  });

  const theme = getTheme();
  const themes: Array<{ v: "light" | "dark" | "system"; t: string; i: LucideIcon }> = [
    { v: "light", t: "Тема: светлая", i: Sun },
    { v: "dark", t: "Тема: тёмная", i: Moon },
    { v: "system", t: "Тема: как в системе", i: MonitorSmartphone },
  ];
  for (const th of themes) {
    if (th.v === theme) continue; // текущую предлагать бессмысленно
    cmds.push({
      id: `action.theme.${th.v}`,
      title: th.t,
      icon: th.i,
      kind: "action",
      keywords: ["тема", "светлая", "тёмная", "theme", "dark", "light"],
      weight: 0.7,
      run: () => setTheme(th.v),
    });
  }

  const hk = singleKeyHotkeysEnabled();
  cmds.push({
    id: "action.hotkeys",
    title: hk ? "Выключить однобуквенные шорткаты" : "Включить однобуквенные шорткаты",
    hint: "аккорды «g …»",
    icon: Keyboard,
    kind: "action",
    keywords: ["клавиши", "шорткаты", "hotkeys", "доступность"],
    weight: 0.5,
    run: () => {
      setSingleKeyHotkeys(!hk);
      ctx.notify(hk ? "Аккорды «g …» выключены" : "Аккорды «g …» включены");
    },
  });

  cmds.push({
    id: "action.logout",
    title: "Выйти из панели",
    icon: LogOut,
    kind: "action",
    keywords: ["логаут", "выход", "logout"],
    weight: 0.4,
    run: ctx.logout,
  });

  return cmds;
}

/**
 * Множитель контекста. Умножается на базовый вес и на нечёткий скор.
 *
 * Навигационная команда в текущий раздел опускается, а не прячется: человек
 * может быть на вкладке и захотеть вернуться на корень раздела. Действия,
 * привязанные к текущему разделу, наоборот, поднимаются.
 */
export function contextBoost(cmd: Command, pathname: string): number {
  const base = cmd.weight ?? 1;
  if (!cmd.section) return base;
  const inSection = pathname === cmd.section || pathname.startsWith(cmd.section + "/");
  if (!inSection) return base;
  return cmd.kind === "action" ? base * 2.5 : base * 0.5;
}
