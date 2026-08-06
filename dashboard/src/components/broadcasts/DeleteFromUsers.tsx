import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { ApiError, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { useEventStream, type BusEvent } from "@/lib/ws";
import { Button, ConfirmDialog } from "@/components/ui";

/**
 * «Удалить из чатов» — стереть уже отправленное у получателей.
 *
 * ЭТО ВТОРОЕ НЕОБРАТИМОЕ ДЕЙСТВИЕ РАЗДЕЛА, И ОНО НЕ ОТМЕНА ОТПРАВКИ.
 * Бот проходит по сохранённым парам (чат, сообщение) и вызывает
 * `deleteMessage`. Telegram позволяет это не всегда и не навсегда: кто
 * успел прочитать — прочитал, а часть сообщений удалить уже нельзя.
 * Поэтому в подтверждении сказано, чего эта операция НЕ делает, — иначе
 * её принимают за кнопку «отозвать рассылку», которой не существует.
 *
 * ПОЧЕМУ ЗДЕСЬ НУЖЕН НАБОР ЧИСЛА. Раньше подтверждение было вторым
 * кликом по соседней кнопке — то есть «да» лежало ровно там, куда уже
 * тянулась рука. Набрать число нельзя на автопилоте (ux-patterns §2.3).
 *
 * ОСТАНОВИТЬ МОЖНО, ОТМЕНИТЬ — НЕТ. Кнопка «Остановить» прекращает
 * обход оставшихся, но не возвращает уже удалённые сообщения.
 */

interface DeleteProgress {
  processed: number;
  total: number;
  deleted: number;
  failed: number;
  status: "running" | "done" | "failed" | "cancelled";
  error?: string;
}

const STATUS_WORDS: Record<DeleteProgress["status"], string> = {
  running: "Удаляю из чатов",
  done: "Удаление закончено",
  failed: "Удаление сорвалось",
  cancelled: "Удаление остановлено",
};

export function DeleteFromUsers({
  broadcastId,
  /**
   * Сколько сообщений вообще можно удалить (has_msg_ids). 0 — нечего,
   * null — неизвестно: число живёт в списке последних рассылок, и по
   * прямой ссылке на давнюю рассылку его там может не оказаться.
   * Разница важна: «удалять нечего» и «мы не знаем, есть ли что» —
   * разные утверждения, и второе не повод обещать первое.
   */
  deletable,
}: {
  broadcastId: number;
  deletable: number | null;
}) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState<DeleteProgress | null>(null);

  const start = useMutation({
    mutationFn: () => endpoints.broadcastDeleteFromUsers(broadcastId),
    onSuccess: (data) => {
      setProgress({
        processed: 0,
        total: data.total_messages,
        deleted: 0,
        failed: 0,
        status: "running",
      });
      setConfirming(false);
    },
    onError: (e: unknown) => {
      toast.error((e as ApiError)?.detail ?? "Не удалось начать удаление");
      setConfirming(false);
    },
  });

  const stop = useMutation({
    mutationFn: () => endpoints.broadcastDeleteCancel(broadcastId),
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось остановить"),
  });

  useEventStream((e: BusEvent) => {
    if (Number(e.broadcast_id ?? 0) !== broadcastId) return;
    const n = (v: unknown) => Number(v ?? 0);

    if (e.type === "broadcast:delete_progress") {
      setProgress({
        processed: n(e.processed),
        total: n(e.total),
        deleted: n(e.deleted),
        failed: n(e.failed),
        status: "running",
      });
    } else if (e.type === "broadcast:delete_done") {
      setProgress({
        processed: n(e.total),
        total: n(e.total),
        deleted: n(e.deleted),
        failed: n(e.failed),
        status: "done",
      });
      toast.success(`Удалено ${fmtNum(n(e.deleted))} из ${fmtNum(n(e.total))}`);
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
    } else if (e.type === "broadcast:delete_failed") {
      setProgress((p) =>
        p ? { ...p, status: "failed", error: String(e.error ?? "") } : p,
      );
      toast.error(String(e.error ?? "Удаление сорвалось"));
    } else if (e.type === "broadcast:delete_cancelled") {
      setProgress((p) => ({
        processed: n(e.processed ?? p?.processed),
        total: n(e.total ?? p?.total),
        deleted: n(e.deleted ?? p?.deleted),
        failed: n(e.failed ?? p?.failed),
        status: "cancelled",
      }));
      toast.info(`Остановлено: удалено ${fmtNum(n(e.deleted))} из ${fmtNum(n(e.total))}`);
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
    }
  });

  // Убираем полосу через восемь секунд после конца, чтобы карточка
  // вернулась к обычному виду. Провал оставляем на экране.
  useEffect(() => {
    if (progress?.status !== "done") return;
    const t = window.setTimeout(() => setProgress(null), 8_000);
    return () => window.clearTimeout(t);
  }, [progress?.status]);

  if (progress) {
    const pct =
      progress.total > 0
        ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
        : 0;
    const bar =
      progress.status === "failed"
        ? "bg-danger"
        : progress.status === "done"
          ? "bg-success"
          : progress.status === "cancelled"
            ? "bg-warning"
            : "bg-accent-9";

    return (
      <div className="space-y-1.5 rounded-md border border-border bg-bg-subtle p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-medium text-fg">{STATUS_WORDS[progress.status]}</span>
          <span className="tabular-nums text-fg-muted">
            удалено {fmtNum(progress.deleted)} из {fmtNum(progress.total)}
            {progress.failed > 0 && (
              <span className="ml-1.5 text-danger">
                не вышло {fmtNum(progress.failed)}
              </span>
            )}
          </span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={STATUS_WORDS[progress.status]}
        >
          <div className={cn("h-full rounded-full", bar)} style={{ width: `${pct}%` }} />
        </div>
        {progress.error && (
          <div className="break-words text-xs text-danger">{progress.error}</div>
        )}
        {progress.status === "running" && (
          <Button size="sm" onClick={() => stop.mutate()} loading={stop.isPending}>
            Остановить
          </Button>
        )}
      </div>
    );
  }

  const known = deletable != null;
  const nothingToDelete = known && deletable <= 0;

  return (
    <>
      <Button
        variant="danger"
        size="sm"
        icon={<Trash2 className="h-3.5 w-3.5" />}
        onClick={() => setConfirming(true)}
        disabled={!known || deletable <= 0}
        // Причина недоступности словами: серая кнопка без объяснения
        // читается как поломка интерфейса.
        title={
          !known
            ? "Не знаем, сколько сообщений этой рассылки можно удалить"
            : deletable > 0
              ? "Стереть сообщения этой рассылки из чатов получателей"
              : "У этой рассылки не сохранены идентификаторы сообщений — стирать нечего"
        }
      >
        Удалить из чатов
      </Button>
      {nothingToDelete && (
        <div className="text-xs text-fg-muted">
          Идентификаторы сообщений не сохранились — из чатов эту рассылку уже не
          убрать.
        </div>
      )}
      {!known && (
        <div className="text-xs text-fg-muted">
          Сколько сообщений можно удалить — неизвестно: этой рассылки нет среди
          последних. Откройте её из списка.
        </div>
      )}

      <ConfirmDialog
        open={confirming}
        onCancel={() => setConfirming(false)}
        onConfirm={() => start.mutate()}
        title="Стереть рассылку из чатов"
        body={
          <>
            Бот попробует удалить{" "}
            <b className="text-fg">{fmtNum(deletable ?? 0)}</b> сообщений из
            чатов получателей. Прочитанное этим не отменяется, а часть сообщений
            Telegram удалить не даст — вернуть удалённые обратно нельзя.
          </>
        }
        confirmLabel={`Удалить ${fmtNum(deletable ?? 0)}`}
        cancelLabel="Оставить"
        destructive
        requireText={String(deletable ?? 0)}
        requireHint={`Наберите число сообщений — ${deletable ?? 0}`}
        loading={start.isPending}
      />
    </>
  );
}
