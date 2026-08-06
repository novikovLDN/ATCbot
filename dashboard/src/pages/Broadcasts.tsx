import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Plus, RefreshCcw } from "lucide-react";

import { endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui";
import { BroadcastList, toBroadcastRow } from "@/components/broadcasts/BroadcastList";
import { BroadcastDetail } from "@/components/broadcasts/BroadcastDetail";
import { ScheduledList } from "@/components/broadcasts/ScheduledList";
import { useSendProgress } from "@/components/broadcasts/useSendProgress";

/**
 * «Рассылки» → вкладка «Отправленные».
 *
 * ПОЧЕМУ ЭТОТ ФАЙЛ КОРОТКИЙ. Он был на 1397 строк и держал в себе
 * список, карточку рассылки, аналитику конверсии, удаление сообщений из
 * чатов, окно планировщика и весь список отложенных — вместе с
 * подписками на шину, работой с московским временем и двумя копиями
 * очистки HTML. Теперь здесь только расстановка и разбор адреса; всё
 * остальное — в `components/broadcasts`. Складывать логику обратно сюда
 * не надо, она уедет к прежней тысяче строк.
 *
 * ДВА СПИСКА, А НЕ ОДИН. Отправленные и отложенные отвечают на разные
 * вопросы: «что мы уже сделали» и «что уйдёт само». Раньше отложенные
 * были узкой колонкой под карточкой рассылки, куда не долистывали, — а
 * именно там видно, что через час уйдёт повторная рассылка, которую
 * никто не помнит. Теперь это равноправный вид с переключателем.
 *
 * СОСТОЯНИЕ ЭКРАНА ЖИВЁТ В АДРЕСЕ: ?view=scheduled и ?id=. Ссылка на
 * конкретную рассылку работает из переписки и из закладки.
 */

export function Broadcasts() {
  const [params, setParams] = useSearchParams();

  const view = params.get("view") === "scheduled" ? "scheduled" : "sent";
  const rawId = params.get("id");
  const selected = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null;

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  const list = useQuery({
    queryKey: ["broadcasts", "recent"],
    queryFn: () => endpoints.broadcastsRecent(200),
    // Приведение к типам — здесь, а не в разметке: сервер отдаёт строки
    // без схемы (см. toBroadcastRow).
    select: (rows) => rows.map(toBroadcastRow),
    refetchInterval: 15_000,
  });

  // Живой ход отправки приходит по шине и нужен и списку, и карточке —
  // поэтому подписка одна, на уровне страницы.
  const progress = useSendProgress();

  // Сколько сообщений этой рассылки можно стереть из чатов. Число лежит
  // в списке, а не в карточке: отдельного запроса за ним нет. Если
  // рассылки в списке нет (прямая ссылка на давнюю), это null —
  // «неизвестно», а не ноль: ноль карточка объявит как «удалять нечего».
  const selectedRow = list.data?.find((b) => b.id === selected);
  const deletable = selectedRow ? selectedRow.has_msg_ids : null;

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Рассылки</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Что уже ушло людям, скольким дошло и кто после этого купил.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            icon={<RefreshCcw className="h-3.5 w-3.5" />}
            onClick={() => list.refetch()}
            loading={list.isFetching && !list.isLoading}
          >
            Обновить
          </Button>
          <Link
            to="/broadcasts/new"
            className="inline-flex min-h-tap items-center gap-2 rounded-md bg-accent-9 px-3 text-base font-medium text-white transition-colors hover:bg-accent-10"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            Собрать рассылку
          </Link>
        </div>
      </header>

      {/* Переключатель вида. Тот же приём, что на «Пользователях»:
          radiogroup, а не вкладки — вкладки раздела уже заняты. */}
      <div
        role="radiogroup"
        aria-label="Что показывать"
        className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
      >
        {(
          [
            ["sent", "Отправленные"],
            ["scheduled", "Отложенные"],
          ] as Array<[string, string]>
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={view === key}
            onClick={() => patch({ view: key === "sent" ? null : key })}
            className={cn(
              "min-h-tap rounded-sm px-3 text-base font-medium transition-colors",
              view === key ? "bg-bg-card text-fg" : "text-fg-muted hover:text-fg",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "scheduled" ? (
        <ScheduledList />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(400px,460px)]">
          {/* На телефоне открытая карточка заменяет список: две колонки на
              375 px не читаются, а сравнивать рассылки построчно незачем. */}
          <div className={selected !== null ? "hidden lg:block" : undefined}>
            <BroadcastList
              query={list}
              selected={selected}
              onSelect={(id) => patch({ id: String(id) })}
              progress={progress}
            />
          </div>

          {selected !== null ? (
            <div className="lg:sticky lg:top-4 lg:self-start">
              <div className="mb-2 lg:hidden">
                <Button size="sm" onClick={() => patch({ id: null })}>
                  ← К списку
                </Button>
              </div>
              <BroadcastDetail
                id={selected}
                deletable={deletable}
                progress={progress[selected]}
                onClose={() => patch({ id: null })}
              />
            </div>
          ) : (
            <div className="hidden rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted lg:block">
              Выберите рассылку слева — текст, доставка и покупки после неё
              откроются здесь.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
