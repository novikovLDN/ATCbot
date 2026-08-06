import { Link } from "react-router-dom";
import {
  Activity,
  CreditCard,
  KeyRound,
  Megaphone,
  UserMinus,
  type LucideIcon,
} from "lucide-react";

import type { AuditEvent } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";
import { actionLabel, personLabel, type EventCategory } from "@/lib/events";
import { StatusBadge } from "@/components/ui";

/**
 * Строка журнала: что произошло, с кем и когда.
 *
 * ЗНАЧОК, А НЕ ЦВЕТ. Категория показана значком и словом в подписи;
 * цвет значка — вспомогательный канал. Красно-зелёная дальтонизация
 * самая частая, и журнал обязан читаться без цвета (research §4.11).
 *
 * ВСЯ СТРОКА — ССЫЛКА НА ПОСТРАДАВШЕГО. Вопрос после «что произошло»
 * всегда «а что с этим человеком сейчас», и ответ на него живёт в
 * карточке пользователя. Если ни адресата, ни автора у записи нет
 * (системные события), строка остаётся текстом — ссылка в никуда хуже
 * её отсутствия.
 *
 * ВРЕМЯ ДВУМЯ СПОСОБАМИ: относительное видно, точное — в подсказке.
 * «12 минут назад» отвечает на «свежее ли это», «06.08.2026, 14:03» — на
 * «совпало ли это с тем сбоем».
 */

const CATEGORY_ICON: Record<EventCategory, LucideIcon> = {
  access: KeyRound,
  money: CreditCard,
  broadcast: Megaphone,
  users: UserMinus,
  other: Activity,
};

const CATEGORY_TONE: Record<EventCategory, string> = {
  access: "text-info",
  money: "text-success",
  broadcast: "text-fg-muted",
  users: "text-risk",
  other: "text-fg-subtle",
};

function iconFor(category: string): LucideIcon {
  return CATEGORY_ICON[category as EventCategory] ?? Activity;
}

function toneFor(category: string): string {
  return CATEGORY_TONE[category as EventCategory] ?? "text-fg-subtle";
}

export function EventRow({ event }: { event: AuditEvent }) {
  const Icon = iconFor(event.category);
  const actor = personLabel(event.actor_id, event.actor_username);
  const target =
    event.target_id != null && event.target_id !== event.actor_id
      ? personLabel(event.target_id, event.target_username)
      : null;

  // Куда вести. Пострадавший важнее автора: он и есть объект события.
  const goTo = event.target_id ?? event.actor_id ?? null;

  const body = (
    <>
      <div className={`mt-0.5 shrink-0 ${toneFor(event.category)}`}>
        <Icon className="h-4 w-4" aria-hidden />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-base font-medium text-fg">
            {actionLabel(event.action)}
          </span>
          {/* result заполняется только у событий жизненного цикла VPN.
              Пустое значение — «не сообщалось», и молчать о нём честнее,
              чем рисовать зелёную галку. */}
          {event.result === "error" && (
            <StatusBadge kind="failure">Ошибка</StatusBadge>
          )}
          {event.result === "success" && (
            <StatusBadge kind="success">Успешно</StatusBadge>
          )}
          {event.source && (
            <span className="rounded-sm bg-bg-subtle px-1.5 py-0.5 font-mono text-2xs text-fg-subtle">
              {event.source}
            </span>
          )}
        </div>

        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-fg-muted">
          <span>
            кто: <span className="tabular-nums">{actor}</span>
          </span>
          {target && (
            <span>
              кому: <span className="tabular-nums">{target}</span>
            </span>
          )}
          {/* Сырое имя действия оставлено видимым: по нему ищут в логах
              сервера, и без него запись невозможно связать с бэкендом. */}
          <span className="font-mono text-2xs text-fg-subtle">{event.action}</span>
        </div>

        {event.details && (
          <div className="mt-1 break-words text-xs text-fg-muted">{event.details}</div>
        )}
      </div>

      <div
        className="shrink-0 text-xs tabular-nums text-fg-subtle"
        title={event.at ? fmtDate(event.at) : undefined}
      >
        {event.at ? fmtRelative(event.at) : "—"}
      </div>
    </>
  );

  if (goTo == null) {
    return <div className="flex items-start gap-3 px-4 py-3">{body}</div>;
  }

  return (
    <Link
      to={`/users?tg=${goTo}`}
      className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-bg-subtle"
    >
      {body}
    </Link>
  );
}
