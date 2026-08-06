/**
 * Разделы автоуведомлений.
 *
 * ПОРЯДОК ЗДЕСЬ — ПОРЯДОК НА ЭКРАНЕ, и он не алфавитный: сверху то, что
 * трогают чаще (триал и подписка — это почти все напоминания), снизу
 * «прочее». Сервер отдаёт список отсортированным по названию раздела,
 * то есть в порядке, который ничего не значит для человека.
 *
 * НАБОР КЛЮЧЕЙ ДОЛЖЕН СОВПАДАТЬ С `VALID_CATEGORIES` НА СЕРВЕРЕ: раздел,
 * которого там нет, не даст создать уведомление. Раздел, который есть на
 * сервере, но отсутствует здесь, не потеряется — список на экране
 * дорисовывает незнакомые разделы в конце по их ключу.
 */

export interface Category {
  key: string;
  label: string;
}

export const CATEGORIES: Category[] = [
  { key: "trial", label: "Пробный период" },
  { key: "subscription", label: "Подписка" },
  { key: "reminder", label: "Напоминания" },
  { key: "welcome", label: "Первое знакомство" },
  { key: "payment", label: "Оплаты" },
  { key: "referral", label: "Приглашения" },
  { key: "gift", label: "Подарки" },
  { key: "other", label: "Прочее" },
];

export function categoryLabel(key: string): string {
  return CATEGORIES.find((c) => c.key === key)?.label ?? key;
}
