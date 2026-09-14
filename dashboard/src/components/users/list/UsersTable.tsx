/**
 * The user listing.
 *
 * This is the screen's missing half. Until now "Пользователи" could only
 * answer "show me this person, whose id I already know" — there was no way
 * to ask who signed up today, whose subscription lapses this week, or who
 * is paying but has never connected.
 *
 * Layout follows the density variables (.dtable reads --row-h,
 * --row-pad-y, --cell-text) rather than fixed padding, so the segmented
 * control resizes every row without React re-rendering any of them.
 * Numeric columns are right-aligned with tabular figures so digits stack
 * by place value; text columns are left-aligned.
 */
import { ArrowDown, ArrowUp, ChevronRight } from "lucide-react";
import type { UserListRow, UserListFilters } from "@/types/user";
import { fmtDate, fmtNum, fmtRelative } from "@/lib/format";
import { tariffLabel } from "@/lib/userDomain";
import { cn } from "@/lib/cn";
import { EmptyState } from "@/components/ui/states";

/**
 * Date only, no clock.
 *
 * `fmtDate` renders "29.07.2026, 21:29", which in a listing column is
 * eleven characters of noise: nobody scans a population by minute, and
 * the time pushed the column wide enough to squeeze out two others on a
 * laptop. The full value stays available on hover, and the detail card —
 * where the minute does matter — keeps using fmtDate.
 */
function dateOnly(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("ru-RU");
}

type SortKey = NonNullable<UserListFilters["sort"]>;

interface Props {
  rows: UserListRow[] | undefined;
  isLoading: boolean;
  isError: boolean;
  filters: UserListFilters;
  onChange: (next: UserListFilters) => void;
  onOpen: (tg: number) => void;
  /** True when any filter is set — picks which empty state to show. */
  hasFilters: boolean;
}

const COLUMNS: {
  key: string;
  label: string;
  sort?: SortKey;
  numeric?: boolean;
  /** Hidden below md — the Priority+ set stays visible on a phone. */
  secondary?: boolean;
}[] = [
  { key: "user", label: "Пользователь" },
  // Folded into the name cell on a phone. Three columns still overflowed
  // 390px, and a date column clipped by the viewport edge reads as a
  // rendering fault rather than as "scroll sideways".
  { key: "status", label: "Подписка", secondary: true },
  { key: "expires_at", label: "Истекает", sort: "expires_at", numeric: true },
  { key: "balance", label: "Баланс", sort: "balance", numeric: true, secondary: true },
  { key: "created_at", label: "Регистрация", sort: "created_at", numeric: true, secondary: true },
  { key: "last_seen_at", label: "Был(а)", sort: "last_seen_at", numeric: true, secondary: true },
];

export function UsersTable({
  rows,
  isLoading,
  isError,
  filters,
  onChange,
  onOpen,
  hasFilters,
}: Props) {
  function toggleSort(key: SortKey) {
    const same = filters.sort === key;
    onChange({
      ...filters,
      sort: key,
      // First click on a new column sorts descending, which is what a
      // reader wants from dates and money almost every time.
      order: same && filters.order === "desc" ? "asc" : "desc",
      offset: 0,
    });
  }

  if (isError) {
    return (
      <div role="alert" className="flex items-center gap-3 rounded-row bg-tile-3 p-4">
        <span className="dot dot-err" aria-hidden="true" />
        <p className="text-[14px]">Не удалось загрузить список пользователей.</p>
      </div>
    );
  }

  return (
    <div>
      <div className="relative -mx-2 overflow-x-auto px-2">
        <table className="dtable">
          <caption className="sr-only">
            Список пользователей. Колонки можно сортировать, строка открывает
            карточку.
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((c) => {
                const active = c.sort && filters.sort === c.sort;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    className={cn(
                      c.numeric && "num",
                      c.secondary && "hidden md:table-cell",
                      active && "text-ink",
                    )}
                    aria-sort={
                      active
                        ? filters.order === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                  >
                    {c.sort ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(c.sort as SortKey)}
                        className={cn(
                          "tap-target inline-flex items-center gap-1 rounded-full outline-none",
                          "transition-colors duration-[var(--dur-instant)] hover:text-ink",
                          "focus-visible:ring-2 focus-visible:ring-accent/45",
                          c.numeric && "flex-row-reverse",
                        )}
                      >
                        {c.label}
                        {/* Direction is always drawn, never implied by
                            column position alone. */}
                        {active &&
                          (filters.order === "asc" ? (
                            <ArrowUp className="h-3 w-3" aria-hidden="true" />
                          ) : (
                            <ArrowDown className="h-3 w-3" aria-hidden="true" />
                          ))}
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                );
              })}
              <th scope="col" className="w-8">
                <span className="sr-only">Открыть</span>
              </th>
            </tr>
          </thead>

          <tbody>
            {isLoading && <SkeletonRows />}

            {!isLoading &&
              rows?.map((r) => <Row key={r.telegram_id} row={r} onOpen={onOpen} />)}
          </tbody>
        </table>
      </div>

      {!isLoading && rows && rows.length === 0 && (
        /* Two distinct empty states. "Nothing here yet" and "your filters
           matched nothing" call for different actions. */
        <EmptyState
          title={hasFilters ? "Под фильтр никто не подошёл." : "Пользователей пока нет."}
          hint={hasFilters ? "Снимите часть условий в строке фильтров выше." : undefined}
        />
      )}
    </div>
  );
}

function Row({ row, onOpen }: { row: UserListRow; onOpen: (tg: number) => void }) {
  const lapsingSoon =
    row.has_active_sub &&
    row.expires_at &&
    new Date(row.expires_at).getTime() - Date.now() < 3 * 86_400_000;

  return (
    <tr
      onClick={() => onOpen(row.telegram_id)}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(row.telegram_id);
      }}
      // Names the row as the visual origin of the detail card, so the
      // browser morphs one into the other instead of cutting. It is what
      // keeps the reader's place in a list of hundreds.
      style={{ viewTransitionName: `user-row-${row.telegram_id}` }}
      className="cursor-pointer outline-none focus-visible:bg-tile-3"
    >
      <td>
        <div className="flex min-w-0 flex-col">
          <span className="flex min-w-0 items-center gap-1.5 truncate text-ink">
            <span className="truncate">{row.username ? `@${row.username}` : "без имени"}</span>
          </span>
          <span className="t-mute tabular truncate font-mono text-[12px]">
            {row.telegram_id}
            {/* The tariff rides along here below md, where its own column
                is hidden — the status of a row is never the thing to drop. */}
            <span className="md:hidden">
              {row.has_active_sub
                ? ` · ${tariffLabel(row.subscription_type)}`
                : " · без подписки"}
            </span>
            {/* A blocked bot is the usual explanation for a notification
                that never arrived; without it that looks like our bug. */}
            {row.is_reachable === false && (
              <span className="ml-1.5 text-warning">бот заблокирован</span>
            )}
          </span>
        </div>
      </td>

      {/* Must carry the same responsive class as its header cell in
          COLUMNS — the two lists are separate, so hiding one without the
          other shifts every column right of it out of alignment. */}
      <td className="hidden md:table-cell">
        {row.has_active_sub ? (
          <span className="text-ink">
            {tariffLabel(row.subscription_type)}
            {row.source === "admin" && (
              <span className="t-mute ml-1.5 text-[12px]">вручную</span>
            )}
          </span>
        ) : (
          <span className="text-ash">—</span>
        )}
      </td>

      <td className="num">
        {row.expires_at ? (
          <span title={fmtDate(row.expires_at)} className={lapsingSoon ? "text-warning" : "t-body"}>
            {dateOnly(row.expires_at)}
          </span>
        ) : (
          <span className="text-ash">—</span>
        )}
      </td>

      <td className="num t-body hidden md:table-cell">
        {row.balance_kopecks ? fmtNum(Math.round(row.balance_kopecks / 100)) : "—"}
      </td>

      <td className="num t-mute hidden md:table-cell" title={fmtDate(row.created_at)}>
        {dateOnly(row.created_at)}
      </td>

      <td className="num t-mute hidden md:table-cell">
        {row.last_seen_at ? fmtRelative(row.last_seen_at) : "—"}
      </td>

      <td>
        <ChevronRight className="h-4 w-4 text-ash" aria-hidden="true" />
      </td>
    </tr>
  );
}

/**
 * Skeleton rows are exactly one --row-h tall, the same as real ones.
 * A shorter placeholder makes the whole table jump when data lands, which
 * costs the reader their scroll position for no reason.
 */
function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} aria-hidden="true">
          {COLUMNS.map((c) => (
            <td key={c.key} className={c.secondary ? "hidden md:table-cell" : undefined}>
              <div className="skeleton h-3 w-full max-w-[120px]" />
            </td>
          ))}
          <td />
        </tr>
      ))}
    </>
  );
}
