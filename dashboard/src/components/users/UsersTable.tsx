import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Crown } from "lucide-react";

import { endpoints, type UserListRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRelative, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardHeader,
  Dash,
  EmptyFailure,
  EmptyFilter,
  LoadingGate,
  SkeletonTable,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
  type Density,
} from "@/components/ui";

/**
 * Плотная таблица пользователей.
 *
 * ЧИСЛО НА ПЛИТКЕ СВОДКИ И ДЛИНА ЭТОГО СПИСКА ОБЯЗАНЫ СОВПАДАТЬ. Для
 * filter=active и filter=expiring_7d сервер отдаёт ровно те строки, по
 * которым сводка посчитала своё число (database/dashboard_summary.py).
 * Никакой фильтрации на клиенте: одно «лишнее» условие здесь — и человек
 * видит 412 на плитке и 380 строк по клику.
 *
 * ОШИБКА И ПУСТОТА ВЫГЛЯДЯТ ПО-РАЗНОМУ. Упавший запрос рисует красную
 * плашку со словами «это отказ запроса, а не отсутствие пользователей»;
 * пустой результат поиска — «ничего не нашлось» с кнопкой сброса, БЕЗ
 * предложения кого-то завести (ux-patterns §3.4: пустой фильтр — не
 * первый запуск).
 *
 * НА ТЕЛЕФОНЕ ТАБЛИЦЫ НЕТ. Записи независимы, сравнивать их по колонкам
 * незачем — вместо горизонтальной прокрутки список карточек (NN/g про
 * таблицы на мобильном, ux-patterns §4.5).
 */

export type UsersFilter = "all" | "active" | "expiring_7d";

const FILTER_TITLES: Record<UsersFilter, { title: string; subtitle: string; empty: string }> = {
  all: {
    title: "Пользователи",
    subtitle: "новые сверху",
    empty: "В базе пока нет ни одного пользователя.",
  },
  active: {
    title: "Активные подписки",
    subtitle: "платные, без триалов и обхода · раньше всего кончаются сверху",
    empty: "Активных платных подписок сейчас нет.",
  },
  expiring_7d: {
    title: "Истекают в ближайшие 7 дней",
    subtitle: "успеть продлить · раньше всего кончаются сверху",
    empty: "В ближайшую неделю не кончается ни одна подписка.",
  },
};

export function UsersTable({
  filter,
  query,
  selected,
  onSelect,
  onResetFilters,
  density,
  limit = 100,
}: {
  filter: UsersFilter;
  query: string;
  selected: number | null;
  onSelect: (telegramId: number) => void;
  onResetFilters: () => void;
  density: Density;
  limit?: number;
}) {
  const list = useQuery({
    queryKey: ["users", "list", filter, query, limit],
    queryFn: () => endpoints.usersList({ filter, q: query || undefined, limit }),
    staleTime: 30_000,
  });

  const t = FILTER_TITLES[filter];
  const items = list.data?.items ?? [];
  const filtered = filter !== "all" || query.length > 0;

  return (
    <Card>
      <CardHeader
        title={t.title}
        subtitle={
          list.data
            ? `${fmtNum(list.data.total)} · ${t.subtitle}`
            : t.subtitle
        }
        actions={
          filtered ? (
            <Button size="sm" onClick={onResetFilters}>
              Снять фильтры
            </Button>
          ) : undefined
        }
      />

      {list.isError ? (
        <div className="p-4">
          <EmptyFailure
            what={t.title.toLowerCase()}
            reason="Список не пришёл. Пустая таблица здесь читалась бы как «никого нет» — это не так, это отказ запроса."
            onRetry={() => list.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={<SkeletonTable rows={8} cols={5} className="m-4 border-0" />}
          message="Выбираю пользователей"
        >
          {/* `list.data &&` обязателен. Первую секунду LoadingGate рисует
              детей, а не скелетон (до секунды человек задержки не
              замечает) — без этой проверки на этой секунде мелькало бы
              «активных подписок нет», то есть неправда. */}
          {list.data && items.length === 0 ? (
            <div className="p-4">
              {query ? (
                <EmptyFilter query={query} onReset={onResetFilters} />
              ) : (
                <div className="py-8 text-center text-base text-fg-muted">{t.empty}</div>
              )}
            </div>
          ) : (
            <>
              {/* Десктоп: плотная таблица с липкой шапкой. */}
              <TableScroll className="hidden max-h-[calc(100vh-260px)] rounded-none border-0 md:block">
                <Table density={density}>
                  <THead>
                    <tr>
                      <TH>Пользователь</TH>
                      <TH>Тариф</TH>
                      <TH>Доступ до</TH>
                      <TH numeric>Баланс</TH>
                      <TH>Состояние</TH>
                      <TH className="w-8" aria-label="Открыть" />
                    </tr>
                  </THead>
                  <TBody>
                    {items.map((row, i) => (
                      <UserTableRow
                        key={row.telegram_id}
                        row={row}
                        first={i === 0}
                        selected={selected === row.telegram_id}
                        onSelect={onSelect}
                      />
                    ))}
                  </TBody>
                </Table>
              </TableScroll>

              {/* Телефон: карточки. */}
              <ul className="divide-y divide-border-subtle md:hidden">
                {items.map((row) => (
                  <li key={row.telegram_id}>
                    <UserCardRow
                      row={row}
                      selected={selected === row.telegram_id}
                      onSelect={onSelect}
                    />
                  </li>
                ))}
              </ul>

              {list.data?.has_more && (
                <div className="border-t border-border-subtle px-4 py-2.5 text-xs text-fg-muted">
                  Показаны первые {fmtNum(items.length)} из {fmtNum(list.data.total)}.
                  Уточните поиск, чтобы увидеть остальных.
                </div>
              )}
            </>
          )}
        </LoadingGate>
      )}
    </Card>
  );
}

/** Состояние подписки словом. Цвет идёт довеском к слову, не вместо него. */
function accessState(row: UserListRow): { kind: "success" | "risk" | "neutral"; label: string } {
  if (!row.subscription_type) return { kind: "neutral", label: "нет подписки" };
  if (row.has_active_sub === false) return { kind: "neutral", label: "истекла" };
  const days = row.expires_at
    ? (new Date(row.expires_at).getTime() - Date.now()) / 86_400_000
    : null;
  if (days !== null && days <= 7) return { kind: "risk", label: "кончается" };
  return { kind: "success", label: "активна" };
}

/** Приписки к тарифу: триал и обход — это не платная подписка. */
function tariffNote(row: UserListRow): string | null {
  const notes: string[] = [];
  if (row.source === "trial") notes.push("триал");
  if (row.is_bypass_only) notes.push("только обход");
  if (row.auto_renew) notes.push("автопродление");
  return notes.length ? notes.join(" · ") : null;
}

function UserTableRow({
  row,
  first,
  selected,
  onSelect,
}: {
  row: UserListRow;
  first: boolean;
  selected: boolean;
  onSelect: (telegramId: number) => void;
}) {
  const state = accessState(row);
  const note = tariffNote(row);

  return (
    <TR
      interactive
      first={first}
      tone={state.kind === "risk" ? "risk" : "none"}
      onActivate={() => onSelect(row.telegram_id)}
      className={cn(selected && "bg-accent-3")}
      aria-current={selected ? "true" : undefined}
    >
      <TD>
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium text-fg">
            {row.username ? `@${row.username}` : `tg:${row.telegram_id}`}
          </span>
          {row.is_vip && (
            <span
              className="inline-flex items-center gap-0.5 rounded-sm bg-warning/12 px-1 py-0.5 text-2xs font-medium text-warning"
              title="VIP-статус"
            >
              <Crown className="h-2.5 w-2.5" aria-hidden />
              VIP
            </span>
          )}
        </div>
        {row.username && (
          <div className="font-mono text-2xs text-fg-subtle">tg:{row.telegram_id}</div>
        )}
      </TD>
      <TD>
        <div className="truncate text-fg">{row.tariff_display ?? <Dash />}</div>
        {note && <div className="truncate text-2xs text-fg-subtle">{note}</div>}
      </TD>
      <TD>
        {row.expires_at ? (
          <>
            <div className="text-fg">{fmtRelative(row.expires_at)}</div>
            <div className="text-2xs text-fg-subtle">{fmtDate(row.expires_at)}</div>
          </>
        ) : (
          <Dash />
        )}
      </TD>
      <TD numeric>
        {row.balance_rubles == null ? <Dash /> : fmtRub(row.balance_rubles)}
      </TD>
      <TD>
        <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
      </TD>
      <TD>
        <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
      </TD>
    </TR>
  );
}

function UserCardRow({
  row,
  selected,
  onSelect,
}: {
  row: UserListRow;
  selected: boolean;
  onSelect: (telegramId: number) => void;
}) {
  const state = accessState(row);
  return (
    <button
      type="button"
      onClick={() => onSelect(row.telegram_id)}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle",
        selected && "bg-accent-3",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate font-medium text-fg">
            {row.username ? `@${row.username}` : `tg:${row.telegram_id}`}
          </span>
          <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
        </div>
        <div className="mt-0.5 truncate text-xs text-fg-muted">
          {[row.tariff_display, tariffNote(row)].filter(Boolean).join(" · ") || "подписки нет"}
        </div>
        <div className="mt-0.5 text-2xs text-fg-subtle">
          {row.expires_at ? `доступ до ${fmtDate(row.expires_at)}` : "доступа нет"}
          {row.balance_rubles != null && ` · баланс ${fmtRub(row.balance_rubles)}`}
        </div>
      </div>
      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
    </button>
  );
}
