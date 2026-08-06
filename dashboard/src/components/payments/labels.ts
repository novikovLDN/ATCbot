import type { StatusKind } from "@/components/ui";
import type { PurchaseRow } from "@/lib/api";

/**
 * Словарь платёжного экрана: как называются провайдеры, типы покупок и
 * состояния. Одно место на два экрана — «Платежи» и «Пользователи»
 * показывают одни и те же покупки, и раньше у них были две копии этих
 * таблиц с расхождениями (в одной «Stars», в другой «Telegram Stars»).
 *
 * Отсюда же берёт подписи карточка пользователя: экран платежей владеет
 * этим словарём, потому что он про платежи, а не про людей.
 */

export const PROVIDER_LABEL: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Telegram Stars",
  telegram_payment: "Telegram",
  lava: "Lava",
  balance: "С баланса",
  unknown: "Не определён",
};

export function providerLabel(p: string | null | undefined): string {
  if (!p) return "Не определён";
  return PROVIDER_LABEL[p] ?? p;
}

/**
 * Внешние ли это деньги.
 *
 * ЭТО НЕ КОСМЕТИКА. Выручка — только внешние поступления: покупка с
 * баланса и автопродление с баланса двигают деньги, которые уже посчитаны
 * при пополнении. Сложите такие строки с остальными — получите выручку
 * больше настоящей, и разойдётесь с плиткой на сводке.
 */
export function isExternalMoney(provider: string | null | undefined): boolean {
  return (provider ?? "") !== "balance";
}

const PURCHASE_TYPE_LABEL: Record<string, string> = {
  subscription: "Подписка",
  traffic_pack: "Пакет ГБ обхода",
  balance_topup: "Пополнение баланса",
  telegram_premium: "Telegram Premium",
  steam: "Steam",
  proxy: "Прокси",
  spotify: "Spotify",
  farm_plot: "Фарм-участок",
};

/** Тарифы. Комбо — отдельный продукт, а не разновидность «Плюс». */
const TARIFF_LABEL: Record<string, string> = {
  basic: "Базовый",
  plus: "Плюс",
  combo_basic: "Комбо Базовый",
  combo_plus: "Комбо Плюс",
  trial: "Пробный",
  biz_starter: "Biz Starter",
  biz_team: "Biz Team",
  biz_business: "Biz Business",
  biz_pro: "Biz Pro",
  biz_enterprise: "Biz Enterprise",
  biz_ultimate: "Biz Ultimate",
};

export function tariffLabel(t: string | null | undefined): string {
  if (!t) return "—";
  return TARIFF_LABEL[t] ?? t;
}

/** Что именно куплено — одной строкой для ячейки таблицы. */
export function purchaseLabel(p: PurchaseRow): string {
  const type = p.purchase_type ?? "subscription";
  if (type === "subscription") {
    const tariff = tariffLabel(p.tariff);
    const period = p.period_days ? ` · ${p.period_days} дн` : "";
    // is_combo из старой схемы: 'plus' с флагом. Если название тарифа уже
    // комбовое, второй раз про это не пишем.
    const combo = p.is_combo && !String(p.tariff ?? "").startsWith("combo_") ? " · комбо" : "";
    return `${tariff}${period}${combo}`;
  }
  if (type === "traffic_pack") {
    return p.country
      ? `Пакет ГБ обхода · ${p.country.toUpperCase()}`
      : "Пакет ГБ обхода";
  }
  if (type === "farm_plot") {
    return p.farm_plot_id ? `Фарм-участок №${p.farm_plot_id}` : "Фарм-участок";
  }
  return PURCHASE_TYPE_LABEL[type] ?? type;
}

/**
 * Состояние покупки: значок, слово и тон. Цвет здесь никогда не один —
 * StatusBadge рисует иконку и слово рядом с ним (research §4.11). Раньше
 * статусы красились tag-цветами с контрастом 1,2:1 — «оплачен» на белом
 * было практически не видно.
 */
export function statusMeta(status: string | null | undefined): {
  kind: StatusKind;
  label: string;
} {
  const s = (status ?? "").toLowerCase();
  if (s === "paid" || s === "approved") return { kind: "success", label: "оплачен" };
  if (s === "pending" || s === "processing") return { kind: "pending", label: "ждёт оплаты" };
  if (s === "expired") return { kind: "neutral", label: "счёт истёк" };
  if (s === "failed" || s === "rejected" || s === "cancelled")
    return { kind: "failure", label: "отказ" };
  return { kind: "neutral", label: status || "—" };
}
