import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcw, ShieldCheck, Wrench } from "lucide-react";

import { ApiError, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyFailure,
  LoadingGate,
  SkeletonCard,
  SkeletonTile,
  StatTile,
} from "@/components/ui";
import { BypassVictim, daysFromNow } from "@/components/events/BypassVictim";

/**
 * «События» → вкладка «Сверка bypass».
 *
 * Второй журнал того же экрана: не «кто что сделал», а «что разъехалось
 * с реальностью и как это починить». Жил отдельным пунктом меню
 * (`pages/BypassAudit.tsx`, 709 строк) — по сути это тот же вопрос «что
 * произошло», только заданный к состоянию подписок (research §4.8:
 * журнал изменений объекта против журнала действий людей).
 *
 * ЧТО ИЗМЕНИЛОСЬ ПРОТИВ СТАРОГО ЭКРАНА
 *   • подтверждение появилось и у точечного восстановления — раньше оно
 *     было только у массового (аудит §4: одно из семи мест без защиты);
 *   • массовое подтверждение просит набрать число строк, а не нажать
 *     вторую кнопку: набор числа нельзя сделать на автопилоте;
 *   • отказ загрузки говорит, чего именно мы не знаем, и даёт «Повторить»
 *     на месте;
 *   • таблицы внутри строки прокручиваются вбок сами и не режутся на
 *     телефоне.
 * Ни одна возможность не убрана.
 */

type Mode = "can_fix" | "no_fix" | "all";

export function EventsBypass() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["bypass-audit"],
    queryFn: endpoints.bypassAuditList,
  });

  const [mode, setMode] = useState<Mode>("can_fix");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  /** telegram_id, по которому открыт вопрос «точно чиним?». */
  const [confirmOne, setConfirmOne] = useState<number | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  const fixOne = useMutation({
    mutationFn: (tg: number) => endpoints.bypassAuditFixOne(tg),
    onSuccess: (data) => {
      toast.success(`Юзер ${data.telegram_id} восстановлен`);
      qc.invalidateQueries({ queryKey: ["bypass-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось восстановить"),
    onSettled: () => setConfirmOne(null),
  });

  const fixAll = useMutation({
    mutationFn: () => endpoints.bypassAuditFixAll(),
    onSuccess: (data) => {
      // Провал по конкретному человеку виден только здесь — говорим о нём
      // словами, а не прячем за общим «готово».
      if (data.failed > 0) {
        toast.error(
          `Восстановлено ${data.fixed} из ${data.total}, не вышло с ${data.failed}`,
        );
      } else {
        toast.success(`Восстановлены все ${data.fixed}`);
      }
      qc.invalidateQueries({ queryKey: ["bypass-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Массовое восстановление упало"),
    onSettled: () => setConfirmAll(false),
  });

  const total = q.data?.total ?? 0;
  const canFix = q.data?.can_fix ?? 0;
  const noFix = Math.max(0, total - canFix);
  const totalGb = q.data?.total_traffic_gb_purchased ?? 0;

  const victims = useMemo(() => {
    const all = q.data?.victims ?? [];
    if (mode === "can_fix") return all.filter((v) => v.can_fix);
    if (mode === "no_fix") return all.filter((v) => !v.can_fix);
    return all;
  }, [q.data, mode]);

  // Кого именно спрашиваем в диалоге. Ищем в свежих данных, а не храним
  // копию строки: пока диалог открыт, список мог обновиться, и подтвердить
  // человек должен то, что есть сейчас.
  const pending =
    confirmOne != null
      ? q.data?.victims.find((v) => v.telegram_id === confirmOne)
      : undefined;
  const pendingDays = daysFromNow(pending?.proposed_expires_at);
  const pendingCurrentDays = daysFromNow(pending?.current_expires_at);

  return (
    <div className="mx-auto max-w-[1100px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-fg">Сверка bypass</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Люди, которым старая покупка трафика перезаписала реальную подписку
            на «premium на 10 лет». По каждому собрана история платежей и
            продлений, и из неё посчитана дата, которая должна была стоять.
          </p>
        </div>
        <Button
          icon={<RefreshCcw className="h-3.5 w-3.5" />}
          onClick={() => q.refetch()}
          loading={q.isFetching && !q.isLoading}
        >
          Перепроверить
        </Button>
      </header>

      {q.isError ? (
        // Отказ проверки — не «пострадавших нет». Разница здесь стоит
        // денег: экран с зелёной галкой закрывают и не возвращаются.
        <EmptyFailure
          what="список расхождений"
          reason="Сверка не ответила. Сколько подписок разъехалось — сейчас неизвестно; это не то же самое, что «всё чисто»."
          onRetry={() => q.refetch()}
        />
      ) : (
        <>
          <LoadingGate
            loading={q.isLoading}
            skeleton={
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[0, 1, 2, 3].map((i) => (
                  <SkeletonTile key={i} />
                ))}
              </div>
            }
            message="Сверяю подписки с историей платежей"
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatTile label="Пострадавших" value={fmtNum(total)} />
              <StatTile
                label="Можно восстановить"
                value={fmtNum(canFix)}
                hint="есть платная история, из которой берётся дата"
              />
              <StatTile
                label="Без истории платежей"
                value={fmtNum(noFix)}
                hint="восстанавливать не из чего"
              />
              <StatTile label="ГБ куплено пострадавшими" value={`${fmtNum(totalGb)} ГБ`} />
            </div>
          </LoadingGate>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-bg-card p-3">
            <div role="radiogroup" aria-label="Кого показывать" className="flex flex-wrap gap-1.5">
              {(
                [
                  ["can_fix", "Можно восстановить", canFix],
                  ["no_fix", "Без истории", noFix],
                  ["all", "Все", total],
                ] as Array<[Mode, string, number]>
              ).map(([key, label, n]) => (
                <button
                  key={key}
                  type="button"
                  role="radio"
                  aria-checked={mode === key}
                  onClick={() => setMode(key)}
                  className={cn(
                    "inline-flex min-h-tap items-center gap-1.5 rounded-md border px-2.5 py-1 text-base transition-colors",
                    mode === key
                      ? "border-accent-9 bg-accent-3 font-medium text-accent-12"
                      : "border-border-control bg-bg-card text-fg-muted hover:bg-bg-subtle hover:text-fg",
                  )}
                >
                  {label}
                  <span className="tabular-nums text-xs text-fg-subtle">{fmtNum(n)}</span>
                </button>
              ))}
            </div>

            <Button
              variant="danger"
              icon={<Wrench className="h-3.5 w-3.5" />}
              disabled={canFix === 0}
              loading={fixAll.isPending}
              onClick={() => setConfirmAll(true)}
            >
              Восстановить всех ({fmtNum(canFix)})
            </Button>
          </div>

          <LoadingGate
            loading={q.isLoading}
            skeleton={
              <div className="space-y-3">
                {[0, 1, 2].map((i) => (
                  <SkeletonCard key={i} lines={2} />
                ))}
              </div>
            }
            message="Собираю истории подписок"
          >
            {victims.length === 0 ? (
              <Card className="flex flex-col items-center gap-3 px-6 py-10 text-center">
                <div className="grid h-10 w-10 place-items-center rounded-lg bg-success/12 text-success">
                  <ShieldCheck className="h-5 w-5" aria-hidden />
                </div>
                <div className="max-w-md">
                  <div className="text-base font-medium text-fg">
                    {total === 0
                      ? "Расхождений нет — сверка прошла и никого не нашла"
                      : "Под выбранный отбор никто не попал"}
                  </div>
                  {total > 0 && (
                    <div className="mt-1 text-base text-fg-muted">
                      Всего в списке {fmtNum(total)}. Смените отбор, чтобы увидеть
                      остальных.
                    </div>
                  )}
                </div>
                {total > 0 && mode !== "all" && (
                  <Button onClick={() => setMode("all")}>Показать всех</Button>
                )}
              </Card>
            ) : (
              <div className="space-y-3">
                {victims.map((v) => (
                  <BypassVictim
                    key={v.telegram_id}
                    v={v}
                    expanded={expanded.has(v.telegram_id)}
                    onToggle={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(v.telegram_id)) next.delete(v.telegram_id);
                        else next.add(v.telegram_id);
                        return next;
                      })
                    }
                    onFix={() => setConfirmOne(v.telegram_id)}
                    // Индикатор на конкретной строке, а не общий на списке:
                    // иначе непонятно, чью запись сейчас чинят.
                    fixing={fixOne.isPending && fixOne.variables === v.telegram_id}
                  />
                ))}
              </div>
            )}
          </LoadingGate>
        </>
      )}

      {/* Точечное восстановление. Раньше выполнялось с первого клика. */}
      <ConfirmDialog
        open={confirmOne != null}
        onCancel={() => setConfirmOne(null)}
        onConfirm={() => confirmOne != null && fixOne.mutate(confirmOne)}
        title="Восстановить срок подписки"
        body={
          pending ? (
            <>
              Пользователю{" "}
              <b className="text-fg">
                {pending.username ? `@${pending.username}` : `tg:${pending.telegram_id}`}
              </b>{" "}
              будет выставлен срок из истории платежей —{" "}
              {pendingDays != null ? `через ${fmtNum(pendingDays)} дн` : "дата из истории"}
              {pending.grace_will_apply ? " (с запасом в один день)" : ""}. Сейчас у
              него{" "}
              {pendingCurrentDays != null
                ? `${fmtNum(pendingCurrentDays)} дн`
                : "неизвестный срок"}
              . Флаг bypass-only снимется.
            </>
          ) : (
            "Запись пропала из списка — обновите сверку."
          )
        }
        confirmLabel="Восстановить"
        cancelLabel="Не трогать"
        loading={fixOne.isPending}
      />

      {/* Массовое. Требует набрать число строк: вторая кнопка нажимается
          на автопилоте, число — нет (ux-patterns §2.3, GitHub Danger Zone). */}
      <ConfirmDialog
        open={confirmAll}
        onCancel={() => setConfirmAll(false)}
        onConfirm={() => fixAll.mutate()}
        title="Восстановить срок сразу всем"
        body={
          <>
            UPDATE пройдёт по {fmtNum(canFix)} строкам подписок. Каждая правится
            своей транзакцией — сбой на одном человеке не остановит остальных, и
            отменить пачку одной кнопкой будет нельзя.
          </>
        }
        confirmLabel={`Применить к ${fmtNum(canFix)}`}
        cancelLabel="Отмена"
        destructive
        requireText={String(canFix)}
        requireHint={`Наберите число строк — ${canFix}`}
        loading={fixAll.isPending}
      />
    </div>
  );
}
