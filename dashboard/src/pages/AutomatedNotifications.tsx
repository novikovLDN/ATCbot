import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, RefreshCcw } from "lucide-react";

import { fmtNum } from "@/lib/format";
import { endpoints } from "@/lib/api";
import {
  Button,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  StatTile,
} from "@/components/ui";
import { Collapsible } from "@/components/Collapsible";
import { CATEGORIES, categoryLabel } from "@/components/broadcasts/categories";
import {
  NotificationRow,
  type NotifRow,
} from "@/components/broadcasts/NotificationRow";
import { NotificationEditor } from "@/components/broadcasts/NotificationEditor";
import { NotificationCreate } from "@/components/broadcasts/NotificationCreate";

/**
 * «Рассылки» → вкладка «Автоуведомления».
 *
 * ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ДВУХ СОСЕДНИХ ВКЛАДОК. Рассылку отправляет
 * человек и один раз; эти сообщения бот отправляет сам и постоянно —
 * напоминания об истечении, приветствия, реакции на оплату. Отсюда
 * разная цена ошибки: опечатка в рассылке уйдёт один раз, опечатка
 * здесь будет уходить каждый день, пока её не заметят. Поэтому правка
 * текста показывает предпросмотр, а «отправить себе» стоит рядом.
 *
 * ВЫКЛЮЧИТЬ — НЕ ТО ЖЕ, ЧТО УДАЛИТЬ, и это главное различие экрана.
 * Выключенное уведомление остаётся со своим текстом и включается
 * обратно одним нажатием; удаление есть только у заготовок, созданных
 * руками. Поэтому у тумблера нет подтверждения, а у удаления — есть, с
 * набором ключа.
 *
 * ОТКАЗ ЗАГРУЗКИ НЕ ВЫГЛЯДИТ КАК ПУСТОЙ СПИСОК. «Уведомлений нет» на
 * упавшем запросе читается как «бот ничего не шлёт» — прямо
 * противоположное правде.
 */

export function AutomatedNotifications() {
  const list = useQuery({
    queryKey: ["automated-notifications"],
    queryFn: () => endpoints.automatedNotifications(),
    refetchInterval: 30_000,
  });

  const [editing, setEditing] = useState<NotifRow | null>(null);
  const [creating, setCreating] = useState(false);

  const rows = useMemo(() => list.data ?? [], [list.data]);

  // Разделы в осмысленном порядке, плюс незнакомые с сервера — в конец,
  // чтобы новый раздел на бэкенде не исчезал с экрана молча.
  const groups = useMemo(() => {
    const byCategory = new Map<string, NotifRow[]>();
    for (const n of rows) {
      const bucket = byCategory.get(n.category) ?? [];
      bucket.push(n);
      byCategory.set(n.category, bucket);
    }
    for (const bucket of byCategory.values()) {
      bucket.sort((a, b) => a.title.localeCompare(b.title, "ru"));
    }

    const known = CATEGORIES.map((c) => c.key);
    const unknown = [...byCategory.keys()].filter((k) => !known.includes(k));
    return [...known, ...unknown]
      .map((key) => ({ key, items: byCategory.get(key) ?? [] }))
      .filter((g) => g.items.length > 0);
  }, [rows]);

  const enabled = rows.filter((n) => n.is_enabled).length;
  const edited = rows.filter((n) => n.has_custom_text).length;

  // Открытая карточка берёт свежую строку из списка, а не копию:
  // список обновляется каждые 30 секунд, и правка должна ложиться на то,
  // что есть сейчас.
  const editingRow = editing ? (rows.find((n) => n.key === editing.key) ?? editing) : null;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-fg">Автоуведомления</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Сообщения, которые бот отправляет сам: напоминания об истечении,
            приветствия, реакции на оплату. Текст и время отправки правятся
            здесь, без выкатки новой версии.
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
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setCreating(true)}
          >
            Своя заготовка
          </Button>
        </div>
      </header>

      {list.isError ? (
        <EmptyFailure
          what="список автоуведомлений"
          reason="Список не ответил. Бот продолжает отправлять уведомления по своим настройкам — мы просто не видим, какие они сейчас."
          onRetry={() => list.refetch()}
        />
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-20" />
                ))}
              </div>
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          }
          message="Читаю список автоуведомлений"
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <StatTile
                label="Отправляются"
                value={fmtNum(enabled)}
                hint={`из ${fmtNum(rows.length)} всего`}
              />
              <StatTile
                label="Выключены"
                value={fmtNum(rows.length - enabled)}
                hint="текст сохранён, бот их не шлёт"
              />
              <StatTile
                label="С правленым текстом"
                value={fmtNum(edited)}
                hint="отличаются от зашитого в коде"
              />
            </div>

            {groups.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted">
                Ни одного автоуведомления не заведено. Обычно они приезжают
                вместе с кодом бота — если список пуст, стоит проверить, дошла
                ли выкатка.
              </div>
            ) : (
              <div className="space-y-2">
                {groups.map((g) => {
                  const on = g.items.filter((n) => n.is_enabled).length;
                  return (
                    <Collapsible
                      key={g.key}
                      title={categoryLabel(g.key)}
                      subtitle={`${fmtNum(g.items.length)} шт. · отправляются ${fmtNum(on)}`}
                      defaultOpen={g.key === "trial"}
                      remember={`autonotif-${g.key}`}
                    >
                      <div className="mt-2 space-y-2">
                        {g.items.map((n) => (
                          <NotificationRow
                            key={n.key}
                            row={n}
                            onEdit={() => setEditing(n)}
                          />
                        ))}
                      </div>
                    </Collapsible>
                  );
                })}
              </div>
            )}
          </div>
        </LoadingGate>
      )}

      {editingRow && (
        <NotificationEditor
          // Ключ в key: при переходе к другому уведомлению компонент
          // пересоздаётся, и в поле не остаётся текст предыдущего.
          key={editingRow.key}
          row={editingRow}
          onClose={() => setEditing(null)}
        />
      )}
      {creating && <NotificationCreate onClose={() => setCreating(false)} />}
    </div>
  );
}
