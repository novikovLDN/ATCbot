import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { X } from "lucide-react";

import { api } from "@/lib/api";
import { fmtDate, fmtNum, fmtRelative } from "@/lib/format";
import { Card, CardHeader, EmptyFailure, LoadingGate, SkeletonTable } from "@/components/ui";

/**
 * Список подписок под фильтр со сводки.
 *
 * Показывается ТОЛЬКО когда в адресе есть ?filter= — плитки зоны B ведут
 * сюда, и без списка их числа были бы мёртвыми ссылками «примерно в
 * раздел». Обычный поиск при этом остаётся на месте, экран не подменяется.
 *
 * ОТБОР ЖИВЁТ НА СЕРВЕРЕ, И ЭТО ВАЖНО. Условия те же, по которым сводка
 * посчитала число (database/dashboard_summary.py). Соберёте список
 * фильтрацией на клиенте — он разойдётся с плиткой на первом же
 * пограничном случае: триал, bypass-only, комбо.
 *
 * Пустой результат здесь — «под фильтр никто не попал», а не «никого
 * нет»: кнопки «завести пользователя» тут быть не должно (ux-patterns
 * §3.4, случай «пустой фильтр» против «первый запуск»).
 */

export type SubscriptionFilter = "active" | "expiring_7d";

const TITLES: Record<SubscriptionFilter, { title: string; subtitle: string; empty: string }> = {
  active: {
    title: "Активные подписки",
    subtitle: "платные, без триалов и bypass-only · раньше всего кончаются сверху",
    empty: "Активных платных подписок сейчас нет.",
  },
  expiring_7d: {
    title: "Истекают в ближайшие 7 дней",
    subtitle: "успеть продлить · раньше всего кончаются сверху",
    empty: "В ближайшую неделю не кончается ни одна подписка.",
  },
};

interface SubscriptionRow {
  telegram_id: number;
  username: string | null;
  subscription_type: string | null;
  is_combo: boolean;
  source: string | null;
  auto_renew: boolean;
  expires_at: string | null;
  activated_at: string | null;
}

export function SubscriptionFilterList({
  filter,
  onReset,
}: {
  filter: SubscriptionFilter;
  onReset: () => void;
}) {
  const list = useQuery({
    queryKey: ["users", "subscriptions", filter],
    queryFn: () =>
      api.get<{ filter: string; items: SubscriptionRow[]; total: number }>(
        `/users/subscriptions?filter=${filter}&limit=200`,
      ),
    staleTime: 30_000,
  });

  const t = TITLES[filter];
  const items = list.data?.items ?? [];

  return (
    <Card>
      <CardHeader
        title={t.title}
        subtitle={
          list.data ? `${fmtNum(list.data.total)} — ${t.subtitle}` : t.subtitle
        }
        actions={
          <button
            type="button"
            onClick={onReset}
            className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            Снять фильтр
          </button>
        }
      />
      <div className="p-4">
        {list.isError ? (
          <EmptyFailure
            what={t.title.toLowerCase()}
            reason="Список не пришёл. Пустая таблица здесь читалась бы как «подписок нет»."
            onRetry={() => list.refetch()}
          />
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={<SkeletonTable rows={6} cols={4} />}
            message="Выбираю подписки"
          >
            {items.length === 0 ? (
              <div className="py-8 text-center text-base text-fg-muted">{t.empty}</div>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {items.map((row) => (
                  <li key={row.telegram_id}>
                    <Link
                      to={`/users?tg=${row.telegram_id}`}
                      className="flex items-center gap-3 rounded-md px-1 py-2.5 transition-colors hover:bg-bg-subtle"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-base text-fg">
                          {row.username ? `@${row.username}` : `tg:${row.telegram_id}`}
                        </div>
                        <div className="truncate text-xs text-fg-muted">
                          {[
                            row.subscription_type,
                            row.is_combo ? "комбо" : null,
                            row.auto_renew ? "автопродление" : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="text-xs text-fg">
                          {row.expires_at ? fmtRelative(row.expires_at) : "—"}
                        </div>
                        <div className="text-2xs text-fg-subtle">
                          {fmtDate(row.expires_at)}
                        </div>
                      </div>
                    </Link>
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
