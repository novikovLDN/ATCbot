import { useCallback, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CreditCard, ShieldCheck, UserPlus } from "lucide-react";

import { endpoints, type SummaryEvent } from "@/lib/api";
import { fmtRelative, fmtRub } from "@/lib/format";
import { useEventStream } from "@/lib/ws";
import { Card, CardHeader, EmptyFailure, LoadingGate, Skeleton } from "@/components/ui";

/**
 * ЗОНА D — лента событий, последние двадцать.
 *
 * Кто что сделал (действия админов) плюс что случилось само (оплата,
 * регистрация). Модель GitHub Audit Log и Stripe Activity (research §4.7,
 * §4.8).
 *
 * ОДИН ИСТОЧНИК ПРАВДЫ. Лента приходит с сервера уже склеенной и
 * отсортированной. WebSocket здесь не рисует строки, а только просит
 * перезапросить: две ленты — серверная и накопленная в браузере — неизбежно
 * разъезжаются, и после перезагрузки страницы человек видит другой набор
 * событий, чем секунду назад.
 *
 * ИНВАЛИДАЦИЯ ПРИТОРМОЖЕНА. Шина шлёт событие на каждую регистрацию и
 * каждый платёж; после рассылки это десятки в минуту. Первое событие
 * обновляет ленту сразу, всё остальное за окно схлопывается в один
 * отложенный запрос — так последнее событие всплеска не теряется.
 */

const INVALIDATE_THROTTLE_MS = 5000;

export function EventFeed() {
  const qc = useQueryClient();
  const events = useQuery({
    queryKey: ["summary", "events"],
    queryFn: () => endpoints.summaryEvents(20),
    refetchInterval: 60_000,
  });

  const lastRef = useRef(0);
  const pendingRef = useRef<number | null>(null);
  const refresh = useCallback(() => {
    const wait = INVALIDATE_THROTTLE_MS - (Date.now() - lastRef.current);
    if (wait <= 0) {
      lastRef.current = Date.now();
      qc.invalidateQueries({ queryKey: ["summary"] });
      return;
    }
    if (pendingRef.current !== null) return;
    pendingRef.current = window.setTimeout(() => {
      pendingRef.current = null;
      lastRef.current = Date.now();
      qc.invalidateQueries({ queryKey: ["summary"] });
    }, wait);
  }, [qc]);

  useEffect(
    () => () => {
      if (pendingRef.current !== null) window.clearTimeout(pendingRef.current);
    },
    [],
  );

  useEventStream((e) => {
    if (e.type === "ping") return;
    refresh();
  });

  return (
    <Card>
      <CardHeader
        title="Что происходило"
        subtitle="последние 20 событий — оплаты, регистрации, действия админов"
      />
      <div className="p-4">
        {events.isError ? (
          <EmptyFailure
            what="ленту событий"
            reason="Журнал не ответил. Пустая лента здесь означала бы, что ничего не происходило, — это не тот случай."
            onRetry={() => events.refetch()}
          />
        ) : (
          <LoadingGate
            loading={events.isLoading}
            skeleton={
              <div className="space-y-2">
                {[0, 1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} className="h-10" />
                ))}
              </div>
            }
            message="Собираю ленту событий"
          >
            {events.data && events.data.items.length === 0 ? (
              <div className="py-8 text-center text-base text-fg-muted">
                Пока тихо. Оплаты, регистрации и действия админов появятся здесь
                сразу, как случатся.
              </div>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {(events.data?.items ?? []).map((e, i) => (
                  <li key={`${e.kind}-${e.at}-${e.actor_id}-${i}`}>
                    <EventRow event={e} />
                  </li>
                ))}
              </ul>
            )}
          </LoadingGate>
        )}
      </div>
    </Card>
  );
}

const KIND_ICON = {
  payment: CreditCard,
  signup: UserPlus,
  admin: ShieldCheck,
} as const;

// Цвет значка — вспомогательный канал. Смысл несёт сам значок и текст
// строки; без цвета лента остаётся читаемой (research §4.11).
const KIND_TONE = {
  payment: "text-success",
  signup: "text-info",
  admin: "text-fg-muted",
} as const;

/** Названия действий из audit_log по-русски. Незнакомое действие не
 *  прячем — показываем как есть: лучше сырой ключ, чем пропавшее событие. */
const ACTION_LABELS: Record<string, string> = {
  admin_revoke: "Доступ отозван",
  admin_reissue: "Ключ перевыпущен",
  admin_reissue_key: "Ключ перевыпущен",
  admin_reissue_all_active: "Массовый перевыпуск ключей",
  admin_create_discount: "Выдана скидка",
  admin_delete_discount: "Скидка снята",
  admin_delete_user: "Пользователь удалён",
  admin_bonus_distribute: "Начислен бонус",
  admin_remnawave_mass_provision: "Массовая выдача в Remnawave",
  admin_test_executed: "Запущен тест",
  vip_granted: "Выдан VIP",
  vip_revoked: "VIP снят",
  broadcast_sent: "Рассылка отправлена",
  broadcast_deleted: "Рассылка удалена у получателей",
  broadcast_delete_cancelled: "Удаление рассылки отменено",
};

const PURCHASE_LABELS: Record<string, string> = {
  subscription: "Подписка",
  balance_topup: "Пополнение баланса",
  gift: "Подарок",
  telegram_premium: "Telegram Premium",
  steam: "Steam",
  proxy: "MTProxy",
};

function EventRow({ event }: { event: SummaryEvent }) {
  const Icon = KIND_ICON[event.kind];
  const who = event.username ? `@${event.username}` : `tg:${event.actor_id ?? "—"}`;

  let title: string;
  let detail: string;
  let to = "/events";

  if (event.kind === "payment") {
    const what = PURCHASE_LABELS[event.title_key ?? ""] ?? event.title_key ?? "Покупка";
    title = `${what} · ${fmtRub(event.amount_rubles)}`;
    detail = [who, event.tariff].filter(Boolean).join(" · ");
    to = `/users?tg=${event.actor_id}`;
  } else if (event.kind === "signup") {
    title = "Новый пользователь";
    detail = who;
    to = `/users?tg=${event.actor_id}`;
  } else {
    title = ACTION_LABELS[event.title_key ?? ""] ?? event.title_key ?? "Действие админа";
    detail = [
      event.target_id ? `кому: tg:${event.target_id}` : null,
      `админ: tg:${event.actor_id ?? "—"}`,
      event.detail,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  return (
    <Link
      to={to}
      className="flex items-center gap-3 rounded-md px-1 py-2.5 transition-colors hover:bg-bg-subtle"
    >
      <Icon className={`h-4 w-4 shrink-0 ${KIND_TONE[event.kind]}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate text-base text-fg">{title}</div>
        <div className="truncate text-xs text-fg-muted">{detail}</div>
      </div>
      <div className="shrink-0 text-xs tabular-nums text-fg-subtle">
        {event.at ? fmtRelative(event.at) : "—"}
      </div>
    </Link>
  );
}
