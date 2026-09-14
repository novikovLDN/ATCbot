/**
 * Branding — name, logo and accent come from GET /api/branding
 * (app/branding.py, env BRAND_*). Nothing in the SPA hardcodes the brand.
 *
 * The accent is written into --c-brand as an "R G B" triplet, which the
 * design tokens resolve into the accent role for the current theme.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

export interface Branding {
  name: string;
  short: string;
  admin_title: string;
  logo_url: string | null;
  primary_color: string;
  support_url?: string;
  channel_url?: string;
  /** The bot's t.me name (config.BOT_USERNAME unless BRAND_BOT_USERNAME is set). */
  bot_username?: string | null;
}

/** Shown for the split second before the request lands, and if it fails. */
export const FALLBACK_BRANDING: Branding = {
  name: "Admin",
  short: "Admin",
  admin_title: "Admin",
  logo_url: null,
  primary_color: "#F2E8C9",
};

export function hexToTriplet(hex: string): string | null {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  if (!m) return null;
  return `${parseInt(m[1], 16)} ${parseInt(m[2], 16)} ${parseInt(m[3], 16)}`;
}

export function applyBranding(b: Branding) {
  const root = document.documentElement;
  const triplet = hexToTriplet(b.primary_color);
  if (triplet) root.style.setProperty("--c-brand", triplet);
  document.title = b.admin_title;
  let meta = document.querySelector('meta[name="application-name"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.setAttribute("name", "application-name");
    document.head.appendChild(meta);
  }
  meta.setAttribute("content", b.admin_title);
  document
    .querySelector('meta[name="apple-mobile-web-app-title"]')
    ?.setAttribute("content", b.short);
}

async function fetchBranding(): Promise<Branding> {
  const res = await fetch("/dashboard/api/branding", { credentials: "include" });
  if (!res.ok) throw new Error(`branding ${res.status}`);
  return (await res.json()) as Branding;
}

export function useBranding(): Branding {
  const q = useQuery({
    queryKey: ["branding"],
    queryFn: fetchBranding,
    staleTime: 10 * 60_000,
    retry: 1,
  });
  const brand = q.data ?? FALLBACK_BRANDING;
  useEffect(() => {
    applyBranding(brand);
  }, [brand]);
  return brand;
}
