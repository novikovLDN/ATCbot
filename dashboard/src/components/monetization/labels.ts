import type { StatusKind } from "@/components/ui";
import type { PromoCodeRow, PromoLinkReward, PromoLinkRow, GiftLinkRow } from "@/lib/api";
import { tariffLabel } from "@/components/payments/labels";

/**
 * Словарь раздела «Монетизация»: как называются периоды, награды и
 * состояния, и как считаются «сколько потрачено» и «когда истекает».
 *
 * ОДНО МЕСТО НА ПЯТЬ ВКЛАДОК. Промокод, промо-ссылка и подарочная ссылка
 * на ГБ — разные сущности с одинаковой механикой: у каждой есть лимит
 * применений и срок. Раньше три экрана считали это по-своему, и
 * расходились: на промокодах исчерпание вообще не срабатывало (счётчик
 * читался из несуществующего поля), на ссылках срок не показывался
 * вовсе. Теперь правила лежат здесь, и добавить их забыть нельзя.
 *
 * НАЗВАНИЯ ТАРИФОВ берутся из components/payments/labels — там же, где
 * их читают «Платежи» и «Пользователи». Вторая копия словаря разошлась
 * бы с первой на первом же новом тарифе.
 */

export { tariffLabel };

/** Период подписки словами. Незнакомое число печатается днями как есть. */
export function periodLabel(days: number): string {
  if (days === 30) return "1 месяц";
  if (days === 90) return "3 месяца";
  if (days === 180) return "6 месяцев";
  if (days === 365) return "1 год";
  if (days === 730) return "2 года";
  return `${days} дн.`;
}

/** Короткая форма периода — для плотной строки таблицы. */
export function periodShort(days: number): string {
  if (days % 365 === 0) return `${days / 365} г.`;
  if (days % 30 === 0) return `${days / 30} мес.`;
  return `${days} дн.`;
}

export const REWARD_LABEL: Record<PromoLinkReward, string> = {
  subscription_days: "Выдача подписки",
  tariff_discount: "Скидка на тарифы",
  bypass_discount: "Скидка на ГБ обхода",
  bypass_gb: "Выдача ГБ обхода",
};

/**
 * Что именно получит человек по промо-ссылке — одной строкой.
 *
 * ТАРИФ ЗДЕСЬ ТОЛЬКО BASIC ИЛИ PLUS. Обработчик диплинка
 * (app/handlers/user/start/marketing_links.py) приводит любой другой
 * тариф к basic, поэтому предлагать комбо в этой форме нельзя: человек
 * выбрал бы «Комбо Плюс», а покупатель получил бы «Базовый».
 */
export function rewardSummary(row: PromoLinkRow): string {
  const meta = row.reward_meta ?? {};
  const value = row.reward_value;
  if (row.reward_type === "subscription_days") {
    const t = String(meta.tariff ?? "basic");
    return `${value} дн. подписки · ${tariffLabel(t)}`;
  }
  if (row.reward_type === "bypass_gb") return `+${value} ГБ обхода`;
  const hours = Number(meta.hours ?? 0);
  const what = row.reward_type === "tariff_discount" ? "на подписку" : "на ГБ обхода";
  return hours > 0 ? `−${value}% ${what}, ${hours} ч.` : `−${value}% ${what}`;
}

/** Расход лимита. max === null означает «без ограничения». */
export interface Usage {
  used: number;
  max: number | null;
  /** 0…1. null, когда предела нет — полоску рисовать не по чему. */
  ratio: number | null;
  exhausted: boolean;
  /** «37 из 100» либо «37 · без ограничения». */
  label: string;
}

export function usageOf(used: number | null | undefined, max: number | null | undefined): Usage {
  const u = Number(used ?? 0);
  const m = max == null || Number(max) <= 0 ? null : Number(max);
  if (m === null) {
    return { used: u, max: null, ratio: null, exhausted: false, label: `${u} · без ограничения` };
  }
  return {
    used: u,
    max: m,
    ratio: Math.max(0, Math.min(1, u / m)),
    exhausted: u >= m,
    label: `${u} из ${m}`,
  };
}

/** Срок жизни. null-срок — «бессрочно», а не «истёк». */
export interface Expiry {
  at: Date | null;
  expired: boolean;
  /** Меньше трёх суток осталось — повод показать это словом. */
  soon: boolean;
  daysLeft: number | null;
}

export function expiryOf(iso: string | null | undefined): Expiry {
  if (!iso) return { at: null, expired: false, soon: false, daysLeft: null };
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) {
    return { at: null, expired: false, soon: false, daysLeft: null };
  }
  const ms = at.getTime() - Date.now();
  const days = ms / 86_400_000;
  return { at, expired: ms <= 0, soon: ms > 0 && days <= 3, daysLeft: days };
}

/**
 * Состояние промокода.
 *
 * ЧЕТЫРЕ ПРИЧИНЫ НЕ РАБОТАТЬ, И ОНИ РАЗНЫЕ. Отключен вручную —
 * включается обратно. Истёк или исчерпан — не включается ничем, кнопка
 * «включить» на таком коде обманывает. Сервер считает совокупный признак
 * is_effective_active сам; здесь он используется как истина, а разбор по
 * причинам нужен только для текста на экране.
 */
export function promoState(row: PromoCodeRow): {
  kind: StatusKind;
  label: string;
  /** Можно ли включить обратно: только вручную отключённый и живой. */
  revivable: boolean;
} {
  const exp = expiryOf(row.expires_at);
  const use = usageOf(row.used_count, row.max_uses);
  if (exp.expired) return { kind: "neutral", label: "истёк", revivable: false };
  if (use.exhausted) return { kind: "neutral", label: "исчерпан", revivable: false };
  const active = row.is_effective_active ?? (row.is_active && !row.deleted_at);
  if (!active) return { kind: "failure", label: "отключён", revivable: true };
  return { kind: "success", label: "действует", revivable: false };
}

/** Состояние подарочной ссылки на ГБ. Те же четыре причины. */
export function giftState(row: GiftLinkRow): { kind: StatusKind; label: string } {
  if (row.deleted_at) return { kind: "neutral", label: "удалена" };
  const exp = expiryOf(row.expires_at);
  if (exp.expired) return { kind: "neutral", label: "истекла" };
  const use = usageOf(row.redemption_count, row.max_uses);
  if (use.exhausted) return { kind: "neutral", label: "исчерпана" };
  if (exp.soon) return { kind: "risk", label: "кончается" };
  return { kind: "success", label: "действует" };
}

/** Состояние маркетинговой ссылки: своей срок есть только у промо. */
export function linkState(row: {
  is_active: boolean;
  expires_at?: string | null;
  used_count?: number | null;
  max_uses_total?: number | null;
}): { kind: StatusKind; label: string } {
  if (!row.is_active) return { kind: "neutral", label: "отключена" };
  const exp = expiryOf(row.expires_at);
  if (exp.expired) return { kind: "neutral", label: "истекла" };
  const use = usageOf(row.used_count, row.max_uses_total);
  if (use.exhausted) return { kind: "neutral", label: "исчерпана" };
  if (exp.soon) return { kind: "risk", label: "кончается" };
  return { kind: "success", label: "работает" };
}
