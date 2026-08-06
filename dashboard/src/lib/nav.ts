/**
 * Единственный источник правды о навигации.
 *
 * Раньше список разделов лежал в трёх местах — Sidebar, MobileNav и нигде для
 * палитры. Сходились они плохо: в сайдбаре было 16 пунктов, в мобильном меню
 * 12, и добавить раздел значило не забыть про оба файла. Теперь конфиг один,
 * а Sidebar / MobileNav / ⌘K читают его.
 *
 * Разделов девять (решение владельца, research §9.2). Ни одна функция при этом
 * не удалена: то, что раньше было отдельным пунктом меню, стало вкладкой
 * внутри раздела, а старые адреса редиректят на новые (см. App.tsx).
 *
 * Группы названы по-русски и по одному основанию — как часто открывают.
 * Раньше рядом стояли «Main», «Маркетинг» и «System»: три языка и три разных
 * основания деления в списке из трёх заголовков.
 */
import {
  LayoutDashboard,
  Users,
  CreditCard,
  Activity,
  TrendingUp,
  Megaphone,
  BadgePercent,
  Wrench,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavTab {
  to: string;
  label: string;
  /** Точное совпадение пути — для вкладки, которая живёт на корне раздела. */
  end?: boolean;
}

export type NavGroup = "daily" | "regular" | "rare";

export interface NavSection {
  /** Корневой путь раздела. Он же адрес первой вкладки. */
  to: string;
  label: string;
  icon: LucideIcon;
  group: NavGroup;
  /** Вторая клавиша аккорда «g …». Первая всегда g. */
  chord: string;
  /** Слова, по которым раздел ищут в палитре, кроме самого названия. */
  keywords?: string[];
  /** Вкладки внутри раздела. Пусто — раздел одностраничный. */
  tabs?: NavTab[];
  /** Показывать в нижней панели телефона как основной пункт. */
  mobilePrimary?: boolean;
}

export const GROUP_LABELS: Record<NavGroup, string> = {
  daily: "Каждый день",
  regular: "Регулярно",
  rare: "Изредка",
};

export const NAV: NavSection[] = [
  {
    to: "/",
    label: "Сводка",
    icon: LayoutDashboard,
    group: "daily",
    chord: "s",
    keywords: ["главная", "выручка", "деньги", "home", "dashboard"],
    mobilePrimary: true,
  },
  {
    to: "/users",
    label: "Пользователи",
    icon: Users,
    group: "daily",
    chord: "u",
    keywords: ["юзеры", "подписки", "клиенты", "users"],
    mobilePrimary: true,
  },
  {
    to: "/payments",
    label: "Платежи",
    icon: CreditCard,
    group: "daily",
    chord: "p",
    keywords: ["оплаты", "провайдеры", "возвраты", "payments"],
    mobilePrimary: true,
  },
  {
    // Слияние двух журналов: «Аудит» и «Bypass Audit» жили отдельными
    // пунктами и оба отвечали на вопрос «что произошло».
    to: "/events",
    label: "События",
    icon: Activity,
    group: "daily",
    chord: "e",
    keywords: ["аудит", "журнал", "лог", "сверка", "bypass", "audit"],
    mobilePrimary: true,
    tabs: [
      // Не «Действия админов»: в журнал попадают и события подписок —
      // выдача ключа, продление, истечение. Название, которое описывает
      // меньше, чем показывает вкладка, вводит в заблуждение.
      { to: "/events", label: "Журнал", end: true },
      { to: "/events/bypass", label: "Сверка bypass" },
    ],
  },
  {
    to: "/analytics",
    label: "Аналитика",
    icon: TrendingUp,
    group: "regular",
    chord: "a",
    keywords: ["метрики", "доход", "статистика", "графики", "analytics"],
    tabs: [
      { to: "/analytics", label: "Метрики и доход", end: true },
      { to: "/analytics/stats", label: "Статистика" },
    ],
  },
  {
    to: "/broadcasts",
    label: "Рассылки",
    icon: Megaphone,
    group: "regular",
    chord: "b",
    keywords: ["сообщения", "уведомления", "автоуведомления", "broadcast"],
    tabs: [
      { to: "/broadcasts", label: "Отправленные", end: true },
      { to: "/broadcasts/new", label: "Новая рассылка" },
      { to: "/broadcasts/auto", label: "Автоуведомления" },
    ],
  },
  {
    to: "/monetization",
    label: "Монетизация",
    icon: BadgePercent,
    group: "regular",
    chord: "m",
    keywords: ["цены", "тарифы", "скидки", "промокоды", "ссылки", "рефералы", "гифт"],
    tabs: [
      { to: "/monetization", label: "Цены и скидки", end: true },
      { to: "/monetization/promo", label: "Промокоды" },
      { to: "/monetization/links", label: "Ссылки" },
      { to: "/monetization/referrals", label: "Рефералы" },
      { to: "/monetization/bgift", label: "Гифт-ГБ" },
    ],
  },
  {
    to: "/service",
    label: "Сервис",
    icon: Wrench,
    group: "rare",
    // s занята «Сводкой», поэтому от serVice.
    chord: "v",
    keywords: ["remnawave", "здоровье", "очередь", "инцидент", "service"],
  },
  {
    to: "/settings",
    label: "Настройки",
    icon: Settings,
    group: "rare",
    chord: "n",
    keywords: ["пароль", "passkey", "push", "settings"],
  },
];

/** Разделы группами, в порядке групп. */
export const NAV_BY_GROUP: Array<{ group: NavGroup; items: NavSection[] }> = (
  ["daily", "regular", "rare"] as NavGroup[]
).map((group) => ({ group, items: NAV.filter((s) => s.group === group) }));

/** Раздел, которому принадлежит путь. Нужен шапке и палитре. */
export function sectionFor(pathname: string): NavSection | undefined {
  if (pathname === "/" || pathname === "") return NAV[0];
  // Сначала самое длинное совпадение — иначе «/» съест всё.
  return [...NAV]
    .filter((s) => s.to !== "/")
    .sort((a, b) => b.to.length - a.to.length)
    .find((s) => pathname === s.to || pathname.startsWith(s.to + "/"));
}
