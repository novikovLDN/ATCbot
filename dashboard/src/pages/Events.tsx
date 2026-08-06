import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { RefreshCcw, ScrollText } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import {
  Button,
  Card,
  CardHeader,
  EmptyFailure,
  EmptyFilter,
  LoadingGate,
  Skeleton,
  Spinner,
} from "@/components/ui";
import {
  EMPTY_FILTER,
  EventFilters,
  describeFilter,
  isFilterActive,
  type EventsFilterValue,
} from "@/components/events/EventFilters";
import { EventRow } from "@/components/events/EventRow";
import { useLiveRefresh } from "@/components/events/useLiveRefresh";

/**
 * «События» → вкладка «Действия админов». Журнал audit_log.
 *
 * ЧТО ЭТО ЗАМЕНИЛО
 * Экран «Аудит» (`pages/Audit.tsx`, 172 строки) — плоский список
 * последних N записей без единого фильтра, с выбором N из четырёх
 * значений. Вторая половина слияния — вкладка «Сверка bypass» рядом:
 * два пункта меню отвечали на один вопрос «что произошло»
 * (research §4.7 — модель GitHub Audit Log).
 *
 * ОШИБКА И ПУСТОТА — РАЗНЫЕ ЭКРАНЫ, И ЭТО ГЛАВНОЕ ЗДЕСЬ
 * Прежний «Аудит» на упавшем запросе рисовал «Журнал пуст» (аудит §3).
 * Пустая лента читается как «ничего не происходило» — на журнале
 * доступов и денег это самая вредная неправда, потому что она
 * успокаивает. Теперь три разных состояния:
 *   отказ           → «Журнал не ответил» и кнопка «Повторить»;
 *   фильтр не сошёлся → названо условие и кнопка «Сбросить фильтры»;
 *   журнал пуст      → так и написано, отдельными словами.
 *
 * ПАГИНАЦИЯ РОСТОМ ОКНА, а не страницами. Журнал читают сверху вниз,
 * пока не найдут нужное; «страница 3 из 12» здесь не значит ничего.
 * Кнопка добирает следующие 50 к тем же условиям.
 */

const PAGE = 50;
const MAX = 500;

/** Пауза перед запросом при наборе. 300 мс — набор слова целиком, а не
 *  запрос на каждую букву; ниже порога осознаваемой задержки (§3.1). */
const TYPING_MS = 300;

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return out;
}

export function Events() {
  const [filter, setFilter] = useState<EventsFilterValue>(EMPTY_FILTER);
  const [limit, setLimit] = useState(PAGE);

  // Текстовые поля отстают на TYPING_MS, чекбоксы и период — нет:
  // нажатие уже закончено, ждать нечего.
  const typed = useDebounced({ who: filter.who, q: filter.q }, TYPING_MS);
  const applied = useMemo(
    () => ({ ...filter, who: typed.who, q: typed.q }),
    [filter, typed],
  );

  // Новые условия — снова первые 50. Иначе после сужения фильтра экран
  // остался бы с окном на 500 записей и запрашивал бы их зря.
  useEffect(() => {
    setLimit(PAGE);
  }, [applied.categories, applied.hours, applied.who, applied.q]);

  const q = useQuery({
    queryKey: ["audit", "events", applied, limit],
    queryFn: () =>
      endpoints.auditEvents({
        limit,
        hours: applied.hours,
        who: applied.who.trim() ? Number(applied.who.trim()) : null,
        q: applied.q,
        categories: applied.categories,
      }),
    refetchInterval: 30_000,
    // Старый список остаётся на месте, пока едет новый: иначе смена
    // фильтра мигала бы скелетоном и терялось бы место, где читали.
    placeholderData: keepPreviousData,
  });

  useLiveRefresh(() => {
    q.refetch();
  });

  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const active = isFilterActive(filter);

  return (
    <div className="mx-auto max-w-[1100px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Что произошло</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Кто выдал доступ, кто снял скидку, кто запустил рассылку. Строка
            ведёт к тому, кого событие коснулось.
          </p>
        </div>
        <Button
          icon={<RefreshCcw className="h-3.5 w-3.5" />}
          onClick={() => q.refetch()}
          loading={q.isFetching && !q.isLoading}
        >
          Обновить
        </Button>
      </header>

      <EventFilters value={filter} onChange={setFilter} counts={q.data?.counts} />

      <Card>
        <CardHeader
          title="Журнал"
          subtitle={
            q.isError
              ? "не загрузился"
              : `показано ${fmtNum(items.length)} из ${fmtNum(total)}`
          }
          actions={q.isFetching && !q.isLoading ? <Spinner /> : undefined}
        />

        {q.isError ? (
          <div className="p-4">
            {/* Именно здесь пустая лента врала бы громче всего. */}
            <EmptyFailure
              what="журнал событий"
              reason="Журнал не ответил. Это не значит, что ничего не происходило, — значит, что мы не смогли посмотреть."
              onRetry={() => q.refetch()}
            />
          </div>
        ) : (
          <LoadingGate
            loading={q.isLoading}
            skeleton={
              <div className="space-y-2 p-4">
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Skeleton key={i} className="h-12" />
                ))}
              </div>
            }
            message="Читаю журнал"
          >
            {items.length === 0 ? (
              <div className="p-4">
                {active ? (
                  <EmptyFilter
                    query={describeFilter(applied)}
                    onReset={() => setFilter(EMPTY_FILTER)}
                  />
                ) : (
                  // Отдельный случай: фильтров нет, отказа нет, записей
                  // тоже нет. Единственный, когда «ничего не происходило»
                  // — правда.
                  <div className="flex flex-col items-center gap-3 rounded-lg border border-border px-6 py-10 text-center">
                    <div className="grid h-10 w-10 place-items-center rounded-lg bg-bg-subtle text-fg-subtle">
                      <ScrollText className="h-5 w-5" aria-hidden />
                    </div>
                    <div className="max-w-sm">
                      <div className="text-base font-medium text-fg">
                        Журнал пуст
                      </div>
                      <div className="mt-1 text-base text-fg-muted">
                        Записей нет — ни одного действия админа и ни одного
                        события подписки ещё не случилось. Появятся здесь сразу.
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {items.map((e) => (
                  <li key={e.id}>
                    <EventRow event={e} />
                  </li>
                ))}
              </ul>
            )}
          </LoadingGate>
        )}

        {!q.isError && q.data?.has_more && (
          <div className="flex items-center justify-center gap-3 border-t border-border-subtle px-4 py-3">
            {limit >= MAX ? (
              <span className="text-xs text-fg-muted">
                Показаны первые {fmtNum(MAX)} записей. Сузьте период или добавьте
                фильтр — глубже журнал не листается.
              </span>
            ) : (
              <Button
                onClick={() => setLimit((n) => Math.min(MAX, n + PAGE))}
                // isPlaceholderData, а не isFetching: иначе кнопка крутилась
                // бы при каждом фоновом обновлении раз в 30 секунд, хотя
                // человек ничего не нажимал.
                loading={q.isPlaceholderData}
              >
                Показать ещё {fmtNum(Math.min(PAGE, MAX - limit))}
              </Button>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
