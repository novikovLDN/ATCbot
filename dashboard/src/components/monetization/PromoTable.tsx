import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints, type PromoCodeRow } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";
import {
  Button,
  ConfirmDialog,
  Dash,
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
import { UsageMeter } from "./UsageMeter";
import { promoState, usageOf } from "./labels";

/**
 * Таблица промокодов.
 *
 * ЧТО ПОЯВИЛОСЬ И ПОЧЕМУ ЭТО НЕ УКРАШЕНИЕ. Раньше строка сообщала факт
 * существования кода: «SUMMER25, −20 %, активен». Сколько применений уже
 * съедено и сколько осталось жить — не было видно вовсе: счётчик читался
 * из поля `uses_count`, которого сервер не отдаёт (там `used_count`), и
 * потому всегда показывал ноль. Заодно не срабатывало «исчерпан»: код с
 * выбранным лимитом выглядел действующим.
 *
 * ВКЛЮЧИТЬ ОБРАТНО МОЖНО НЕ ВСЁ. Истёкший и исчерпанный код не оживить
 * ничем — кнопка «включить» на них была бы обманом. Она показывается
 * только у кодов, отключённых вручную (promoState.revivable).
 *
 * ОТКЛЮЧЕНИЕ ЧЕРЕЗ ДИАЛОГ, ВКЛЮЧЕНИЕ — БЕЗ НЕГО. Отключение бьёт по
 * продажам: люди, которым код уже разослали, перестанут его применять.
 * Включение обратно безобидно и обратимо, и диалог на нём был бы тем
 * самым «подтверждением на каждый чих», после которого перестают читать
 * все диалоги (NN/g).
 */
export function PromoTable({
  rows,
  density,
}: {
  rows: PromoCodeRow[];
  density: Density;
}) {
  return (
    <>
      {/* Десктоп: плотная таблица. */}
      <TableScroll className="hidden max-h-[calc(100vh-300px)] rounded-none border-0 md:block">
        <Table density={density}>
          <THead>
            <tr>
              <TH>Код</TH>
              <TH numeric>Скидка</TH>
              <TH>Применений и срок</TH>
              <TH>Состояние</TH>
              <TH className="w-40" aria-label="Действия" />
            </tr>
          </THead>
          <TBody>
            {rows.map((row, i) => (
              <PromoRow key={row.id ?? row.code} row={row} first={i === 0} />
            ))}
          </TBody>
        </Table>
      </TableScroll>

      {/* Телефон: карточки. Сравнивать коды по колонкам незачем. */}
      <ul className="divide-y divide-border-subtle md:hidden">
        {rows.map((row) => (
          <li key={row.id ?? row.code} className="px-4 py-3">
            <PromoCard row={row} />
          </li>
        ))}
      </ul>
    </>
  );
}

/** Мутации отключения и включения — общие для строки и карточки. */
function usePromoActions(row: PromoCodeRow) {
  const qc = useQueryClient();
  const id = Number(row.id ?? 0);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["promo"] });

  const off = useMutation({
    mutationFn: () => endpoints.promoDeactivate(id),
    onSuccess: () => {
      toast.success(`Промокод ${row.code} отключён`);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось отключить код"),
  });

  const on = useMutation({
    mutationFn: () => endpoints.promoReactivate(id),
    onSuccess: () => {
      toast.success(`Промокод ${row.code} снова применяется`);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось включить код"),
  });

  return { off, on, id };
}

/** Текст последствия для диалога отключения — с числами, не «вы уверены?». */
function offConsequence(row: PromoCodeRow): string {
  const use = usageOf(row.used_count, row.max_uses);
  const left = use.max === null ? null : Math.max(0, use.max - use.used);
  const spent = `Применён ${use.label}`;
  const rest =
    left === null
      ? "лимита нет"
      : left === 0
        ? "лимит уже выбран"
        : `осталось ${left}`;
  return `${spent} (${rest}). Люди, которым код уже разослали, перестанут получать скидку −${row.discount_percent}% сразу.`;
}

function PromoRow({ row, first }: { row: PromoCodeRow; first: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const state = promoState(row);
  const { off, on } = usePromoActions(row);
  const canDisable = state.label === "действует";

  return (
    <TR first={first} tone={state.kind === "failure" ? "failure" : "none"}>
      <TD>
        <span className="font-mono text-base font-medium text-fg">{row.code}</span>
      </TD>
      <TD numeric>
        <span className="font-semibold tabular-nums text-fg">−{row.discount_percent}%</span>
      </TD>
      <TD>
        <UsageMeter
          used={row.used_count}
          max={row.max_uses}
          expiresAt={row.expires_at}
          className="min-w-[190px]"
        />
      </TD>
      <TD>
        <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
      </TD>
      <TD>
        <div className="flex justify-end gap-1">
          {canDisable && (
            <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>
              Отключить
            </Button>
          )}
          {state.revivable && (
            <Button size="sm" onClick={() => on.mutate()} loading={on.isPending}>
              Включить
            </Button>
          )}
        </div>
        <ConfirmDialog
          open={confirm}
          onCancel={() => setConfirm(false)}
          onConfirm={() => {
            off.mutate();
            setConfirm(false);
          }}
          loading={off.isPending}
          title={`Отключить промокод ${row.code}`}
          confirmLabel="Отключить код"
          cancelLabel="Оставить работать"
          body={offConsequence(row)}
        />
      </TD>
    </TR>
  );
}

function PromoCard({ row }: { row: PromoCodeRow }) {
  const [confirm, setConfirm] = useState(false);
  const state = promoState(row);
  const { off, on } = usePromoActions(row);
  const canDisable = state.label === "действует";

  return (
    <div className={cn("flex flex-col gap-2")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-base font-medium text-fg">{row.code}</span>
        <span className="font-semibold tabular-nums text-fg">−{row.discount_percent}%</span>
        <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
      </div>
      <UsageMeter used={row.used_count} max={row.max_uses} expiresAt={row.expires_at} />
      <div className="text-2xs text-fg-subtle">
        создан {row.created_at ? fmtDate(row.created_at) : <Dash />}
      </div>
      <div className="flex gap-2">
        {canDisable && (
          <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>
            Отключить
          </Button>
        )}
        {state.revivable && (
          <Button size="sm" onClick={() => on.mutate()} loading={on.isPending}>
            Включить
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          off.mutate();
          setConfirm(false);
        }}
        loading={off.isPending}
        title={`Отключить промокод ${row.code}`}
        confirmLabel="Отключить код"
        cancelLabel="Оставить работать"
        body={offConsequence(row)}
      />
    </div>
  );
}
