/**
 * Icons of the sections behind «Ещё». Kept out of lib/nav.ts so they load
 * with the lazy «Ещё» screen / desktop sidebar, not with the entry chunk.
 */
import {
  Activity,
  BarChart3,
  BellRing,
  Circle,
  FlaskConical,
  Gauge,
  Gift,
  Handshake,
  Link2,
  Megaphone,
  ScrollText,
  Server,
  Settings,
  ShieldAlert,
  Tag,
  TicketPercent,
  UserSearch,
  Wrench,
  type LucideIcon,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  "/panel": Server,
  "/users": UserSearch,
  "/engagement": Activity,
  "/statistics": BarChart3,
  "/broadcasts": Megaphone,
  "/automated-notifications": BellRing,
  "/pricing": Tag,
  "/promo": TicketPercent,
  "/links": Link2,
  "/referrals": Handshake,
  "/bgift": Gift,
  "/beta-applications": FlaskConical,
  "/audit": ScrollText,
  "/bypass-audit": ShieldAlert,
  "/traffic-audit": Gauge,
  "/service": Wrench,
  "/settings": Settings,
};

export function navIcon(to: string): LucideIcon {
  return ICONS[to] ?? Circle;
}
