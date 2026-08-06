/**
 * Словарь событий: одни и те же названия для ленты на «Сводке» и для
 * экрана «События».
 *
 * ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ
 * Названий действий в audit_log под сорок штук, и они пишутся из десятка
 * мест бота. Пока карта названий лежала внутри страницы, её копий было
 * две — в `pages/Audit.tsx` и в `components/summary/EventFeed.tsx`, — и
 * они уже разошлись: одна знала `admin_grant`, другая `vip_granted`.
 * Одно и то же событие называлось на двух экранах по-разному.
 *
 * ПРАВИЛО «ЧТО СОБЫТИЕ, А ЧТО ФОН»
 * `admin_view_*` и `*_viewed` пишутся при каждом открытии экрана в боте,
 * `reminder_sent` — сотнями в сутки из воркера. Отсекает их сервер
 * (`database/dashboard_events.py`, константа NOISE_SQL — одна на оба
 * запроса). Здесь тот же список продублирован ТОЛЬКО как страховка для
 * данных из старых ответов; выкидывать записи на клиенте вместо сервера
 * нельзя — сломается счётчик «показано N из M».
 *
 * НЕЗНАКОМОЕ ДЕЙСТВИЕ НЕ ПРЯЧЕТСЯ. Нет перевода — печатаем сырой ключ.
 * Лучше `admin_frobnicate` на экране, чем пропавшее событие: журнал, из
 * которого что-то тихо исчезает, бесполезен.
 */

/** Категория считается на сервере (CASE в SQL), здесь только названия. */
export type EventCategory = "access" | "money" | "broadcast" | "users" | "other";

export const EVENT_CATEGORIES: EventCategory[] = [
  "access",
  "money",
  "broadcast",
  "users",
  "other",
];

export const CATEGORY_LABELS: Record<EventCategory, string> = {
  access: "Доступ и ключи",
  money: "Деньги и скидки",
  broadcast: "Рассылки",
  users: "Пользователи",
  other: "Прочее",
};

/** Короткая подпись под фильтром — что именно попадает в категорию. */
export const CATEGORY_HINTS: Record<EventCategory, string> = {
  access: "выдача, отзыв, перевыпуск ключей, продления",
  money: "платежи, скидки, VIP, бонусы, рефералы",
  broadcast: "отправка и удаление рассылок",
  users: "удаление и правки профилей",
  other: "тесты и всё, что не попало в остальные",
};

/** Названия действий из audit_log по-русски. */
export const ACTION_LABELS: Record<string, string> = {
  // Доступ и ключи
  admin_grant: "Выдан доступ",
  admin_revoke: "Доступ отозван",
  admin_reissue: "Ключ перевыпущен",
  admin_reissue_key: "Ключ перевыпущен",
  admin_reissue_all_active: "Массовый перевыпуск ключей",
  admin_remnawave_mass_provision: "Массовая выдача в Remnawave",
  admin_switch_tariff: "Тариф сменён",
  ADMIN_SWITCH_TO_PLUS: "Тариф → Plus",
  ADMIN_SWITCH_TO_BASIC: "Тариф → Basic",
  subscription_created: "Подписка создана",
  subscription_renewed: "Подписка продлена",
  vpn_add_user: "Ключ выдан в панели",
  vpn_remove_user: "Ключ удалён из панели",
  vpn_renew: "Подписка продлена в панели",
  vpn_expire: "Подписка истекла",

  // Деньги
  payment_approved: "Платёж подтверждён",
  payment_received: "Платёж получен",
  payment_rejected: "Платёж отклонён",
  payment_subscription_activation_failed: "Оплата прошла, доступ не выдался",
  telegram_payment_successful: "Оплата через Telegram",
  purchase_rejected_due_to_stale_context: "Покупка отклонена: устаревший заказ",
  admin_create_discount: "Выдана скидка",
  admin_delete_discount: "Скидка снята",
  admin_bonus_distribute: "Начислен бонус",
  vip_granted: "Выдан VIP",
  vip_revoked: "VIP снят",
  promo_consumed: "Промокод использован",
  referral_reward: "Реферальная награда",
  withdrawal_admin_notify_failed: "Не ушло уведомление о выплате",

  // Рассылки
  broadcast_sent: "Рассылка отправлена",
  broadcast_created: "Рассылка создана",
  broadcast_deleted: "Рассылка удалена у получателей",
  broadcast_delete_cancelled: "Удаление рассылки отменено",

  // Пользователи
  admin_delete_user: "Пользователь удалён",
  user_deleted: "Пользователь удалён",

  // Прочее
  admin_test_executed: "Запущен тест",
};

/** Типы покупок — для ленты на «Сводке» и для подписи платежей. */
export const PURCHASE_LABELS: Record<string, string> = {
  subscription: "Подписка",
  balance_topup: "Пополнение баланса",
  gift: "Подарок",
  telegram_premium: "Telegram Premium",
  steam: "Steam",
  proxy: "MTProxy",
};

/** Человеческое имя действия либо сырой ключ. */
export function actionLabel(action: string | null | undefined): string {
  if (!action) return "Событие без названия";
  return ACTION_LABELS[action] ?? action;
}

export function purchaseLabel(kind: string | null | undefined): string {
  if (!kind) return "Покупка";
  return PURCHASE_LABELS[kind] ?? kind;
}

/**
 * Фон, а не событие. Дубликат серверного правила — держите в согласии с
 * NOISE_SQL в `database/dashboard_events.py`.
 */
export function isBackgroundAction(action: string | null | undefined): boolean {
  if (!action) return false;
  return (
    action.startsWith("admin_view") ||
    action.endsWith("_viewed") ||
    action === "reminder_sent"
  );
}

/** Как звать человека: @ник, если он есть, иначе telegram_id. */
export function personLabel(
  id: number | null | undefined,
  username: string | null | undefined,
): string {
  if (username) return `@${username}`;
  if (id != null) return `tg:${id}`;
  return "—";
}
