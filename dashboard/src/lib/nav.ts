/**
 * Navigation map of the admin, one place for the tab bar, the «Ещё»
 * screen, the desktop sidebar and the compact navigation-bar title.
 *
 * Only the tab-bar icons live here (they are in the entry chunk); icons
 * of the other sections are in components/navIcons.ts, imported only by
 * the lazy «Ещё» screen and the lazy desktop sidebar.
 */
import { HeartPulse, LayoutGrid, Users, Wallet, type LucideIcon } from "lucide-react";
import { endpoints } from "@/lib/api";
import { auth } from "@/lib/auth";

export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

export interface TabItem extends NavItem {
  icon: LucideIcon;
}

/** Bottom tab bar (plus «Ещё»): the four screens opened most. */
export const TABS: TabItem[] = [
  { to: "/", label: "Обзор", icon: LayoutGrid, end: true },
  { to: "/money", label: "Деньги", icon: Wallet },
  { to: "/subscribers", label: "Подписчики", icon: Users },
  { to: "/health", label: "Здоровье", icon: HeartPulse },
];

export const MORE_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Данные",
    items: [
      { to: "/panel", label: "Панель Remnawave" },
      { to: "/users", label: "Пользователи" },
      { to: "/engagement", label: "Вовлечённость" },
      { to: "/statistics", label: "Продажи по тарифам" },
    ],
  },
  {
    title: "Сообщения",
    items: [
      { to: "/broadcasts", label: "Рассылки" },
      { to: "/automated-notifications", label: "Автоуведомления" },
    ],
  },
  {
    title: "Продажи",
    items: [
      { to: "/pricing", label: "Цены и скидки" },
      { to: "/promo", label: "Промокоды" },
      { to: "/links", label: "Ссылки" },
      { to: "/referrals", label: "Рефералы" },
      { to: "/bgift", label: "Гифт-ГБ" },
      { to: "/beta-applications", label: "VPN-Инноватор" },
    ],
  },
  {
    title: "Контроль",
    items: [
      { to: "/audit", label: "Журнал действий" },
      { to: "/traffic-audit", label: "Аудит трафика" },
      { to: "/service", label: "Сервис" },
    ],
  },
  {
    title: "Приложение",
    items: [{ to: "/settings", label: "Настройки" }],
  },
];

export const MORE_ITEMS: NavItem[] = MORE_GROUPS.flatMap((g) => g.items);

/** v3 names, kept for any caller that still imports them. */
export const PRIMARY_NAV: NavItem[] = TABS;
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
  const all: NavItem[] = [...TABS, ...MORE_ITEMS];
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
