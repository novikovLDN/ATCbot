/**
 * Navigation map of the admin, one place for the tab bar, the «Ещё»
 * screen, the desktop sidebar and the compact navigation-bar title.
 */
import {
  Activity,
  BarChart3,
  BellRing,
  FlaskConical,
  Gauge,
  Gift,
  Handshake,
  HeartPulse,
  LayoutGrid,
  Link2,
  Megaphone,
  ScrollText,
  Server,
  Settings,
  ShieldAlert,
  Tag,
  TicketPercent,
  UserSearch,
  Users,
  Wallet,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { auth } from "@/lib/auth";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

/** Bottom tab bar (plus «Ещё»): the four screens opened most. */
export const TABS: NavItem[] = [
  { to: "/", label: "Обзор", icon: LayoutGrid, end: true },
  { to: "/money", label: "Деньги", icon: Wallet },
  { to: "/subscribers", label: "Подписчики", icon: Users },
  { to: "/health", label: "Здоровье", icon: HeartPulse },
];

export const MORE_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Данные",
    items: [
      { to: "/panel", label: "Панель Remnawave", icon: Server },
      { to: "/users", label: "Пользователи", icon: UserSearch },
      { to: "/engagement", label: "Вовлечённость", icon: Activity },
      { to: "/statistics", label: "Продажи по тарифам", icon: BarChart3 },
    ],
  },
  {
    title: "Сообщения",
    items: [
      { to: "/broadcasts", label: "Рассылки", icon: Megaphone },
      { to: "/automated-notifications", label: "Автоуведомления", icon: BellRing },
    ],
  },
  {
    title: "Продажи",
    items: [
      { to: "/pricing", label: "Цены и скидки", icon: Tag },
      { to: "/promo", label: "Промокоды", icon: TicketPercent },
      { to: "/links", label: "Ссылки", icon: Link2 },
      { to: "/referrals", label: "Рефералы", icon: Handshake },
      { to: "/bgift", label: "Гифт-ГБ", icon: Gift },
      { to: "/beta-applications", label: "VPN-Инноватор", icon: FlaskConical },
    ],
  },
  {
    title: "Контроль",
    items: [
      { to: "/audit", label: "Журнал действий", icon: ScrollText },
      { to: "/bypass-audit", label: "Bypass-аудит", icon: ShieldAlert },
      { to: "/traffic-audit", label: "Аудит трафика", icon: Gauge },
      { to: "/service", label: "Сервис", icon: Wrench },
    ],
  },
  {
    title: "Приложение",
    items: [{ to: "/settings", label: "Настройки", icon: Settings }],
  },
];

export const MORE_ITEMS: NavItem[] = MORE_GROUPS.flatMap((g) => g.items);

/** v3 names, kept for any caller that still imports them. */
export const PRIMARY_NAV = TABS;
export const MORE_NAV = MORE_ITEMS;

function matches(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

/** True when the path belongs to the «Ещё» tab. */
export function inMore(pathname: string): boolean {
  return pathname === "/more" || MORE_ITEMS.some((i) => matches(pathname, i.to));
}

/** Title for the compact navigation bar. */
export function titleFor(pathname: string): string {
  if (pathname === "/more") return "Ещё";
  if (pathname === "/broadcasts/new") return "Новая рассылка";
  const all = [...TABS, ...MORE_ITEMS];
  const hit = all.find((i) => (i.end ? pathname === i.to : matches(pathname, i.to)));
  return hit?.label ?? "";
}

/** Back button of a pushed screen (phones): «‹ Ещё», «‹ Рассылки». */
export function backFor(pathname: string): { to: string; label: string } | null {
  if (pathname === "/broadcasts/new") return { to: "/broadcasts", label: "Рассылки" };
  if (pathname !== "/more" && inMore(pathname)) return { to: "/more", label: "Ещё" };
  return null;
}

export async function logout(): Promise<void> {
  try {
    await endpoints.authLogout();
  } catch {
    //
  }
  auth.clear();
  window.location.assign("/dashboard/");
}
