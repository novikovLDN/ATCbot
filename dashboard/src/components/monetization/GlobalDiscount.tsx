import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints, type TariffPriceRow } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Input,
  StatusBadge,
} from "@/components/ui";
import { PriceDiff, applyDiscount, type PriceChange } from "./PriceDiff";
import { expiryOf } from "./labels";

/**
 * Глобальная скидка: одна ручка, которая двигает все цены сразу.
 *
 * ПОЭТОМУ ПРЕДПРОСМОТР ЗДЕСЬ ШИРЕ, ЧЕМ У ОТДЕЛЬНОЙ ЦЕНЫ. Ошибка в одной
 * цене стоит одного тарифа; ошибка здесь — всего прайса. Перед
 * сохранением показывается «было → станет» по КАЖДОЙ затронутой паре
 * тариф+период, а не проценты и не «вы уверены?».
 *
 * ВЫКЛЮЧЕНИЕ — ТОЖЕ ИЗМЕНЕНИЕ ЦЕН, и оно опаснее включения: цены разом
 * растут, и человек, пришедший «просто выключить акцию», должен увидеть,
 * на сколько именно. Диалог тот же самый, список тот же самый.
 *
 * СРОК ПРОВЕРЯЕТСЯ ДО ОТПРАВКИ. Сервер отвергает дату в прошлом (400), но
 * узнать об этом из тоста после нажатия — плохой обмен: поле подсвечивается
 * сразу, введённое сохраняется.
 */
export function GlobalDiscount({
  current,
  rows,
}: {
  current: {
    global_discount_percent: number;
    discount_reason: string | null;
    discount_until_at: string | null;
  } | undefined;
  /** Только правимые строки прайса: комбо скидка не трогает. */
  rows: TariffPriceRow[];
}) {
  const qc = useQueryClient();

  const percentNow = current?.global_discount_percent ?? 0;
  const exp = expiryOf(current?.discount_until_at);
  const active = percentNow > 0 && !exp.expired;

  const [open, setOpen] = useState(false);
  const [percent, setPercent] = useState(String(percentNow > 0 ? percentNow : 10));
  const [reason, setReason] = useState(current?.discount_reason ?? "");
  const [until, setUntil] = useState(
    current?.discount_until_at ? isoToInput(current.discount_until_at) : "",
  );
  const [confirm, setConfirm] = useState<"save" | "clear" | null>(null);

  const parsed = Number(percent.trim());
  const percentValid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 99;
  const percentError =
    percent.trim() === "" || percentValid ? undefined : "Целое число от 1 до 99";

  const untilDate = until ? new Date(until) : null;
  const untilInPast = untilDate !== null && !Number.isNaN(untilDate.getTime())
    ? untilDate.getTime() <= Date.now()
    : false;
  const untilError = untilInPast
    ? "Срок уже прошёл — скидка выключилась бы в момент включения"
    : undefined;

  const canSubmit = percentValid && !untilInPast;

  const invalidate = () => qc.invalidateQueries({ queryKey: ["pricing"] });

  const save = useMutation({
    mutationFn: () =>
      endpoints.pricingSetGlobalDiscount({
        percent: parsed,
        reason: reason.trim() || null,
        until_at_iso: until ? new Date(until).toISOString() : null,
      }),
    onSuccess: () => {
      toast.success(`Скидка −${parsed}% включена на все тарифы`);
      setConfirm(null);
      setOpen(false);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось включить скидку"),
  });

  const clear = useMutation({
    mutationFn: () => endpoints.pricingClearGlobalDiscount(),
    onSuccess: () => {
      toast.success("Скидка отключена — цены вернулись к базовым");
      setConfirm(null);
      setOpen(false);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось отключить скидку"),
  });

  /** Что станет с прайсом при указанном проценте. */
  const changesFor = (nextPercent: number): PriceChange[] =>
    rows.map((r) => ({
      tariff: r.tariff,
      periodDays: r.period_days,
      from: r.effective_price,
      to: applyDiscount(r.base_price, nextPercent),
    }));

  return (
    <Card>
      <CardHeader
        title="Глобальная скидка"
        subtitle="один процент на все обычные и бизнес-тарифы · комбо не затрагивает"
        actions={
          <div className="flex items-center gap-2">
            {active && (
              <Button size="sm" variant="ghost" onClick={() => setConfirm("clear")}>
                Отключить
              </Button>
            )}
            <Button
              size="sm"
              variant={active ? "secondary" : "primary"}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Свернуть" : active ? "Изменить" : "Включить скидку"}
            </Button>
          </div>
        }
      />

      <CardBody className="space-y-3">
        {active ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-2xl font-semibold tabular-nums text-fg">
              −{percentNow}%
            </span>
            <StatusBadge kind="success">действует</StatusBadge>
            {current?.discount_reason && (
              <span className="text-base text-fg-muted">
                подпись покупателю: «{current.discount_reason}»
              </span>
            )}
            <span className="w-full text-xs text-fg-subtle">
              {exp.at === null ? (
                "бессрочно — выключается только вручную"
              ) : (
                <>
                  {exp.soon ? (
                    <span className="font-medium text-risk">
                      истекает {fmtRelative(current?.discount_until_at)}
                    </span>
                  ) : (
                    <>истекает {fmtRelative(current?.discount_until_at)}</>
                  )}{" "}
                  · {fmtDate(current?.discount_until_at)}
                </>
              )}
            </span>
          </div>
        ) : (
          <div className="text-base text-fg-muted">
            {percentNow > 0 && exp.expired
              ? "Срок скидки истёк — покупатели платят базовую цену."
              : "Скидки нет: покупатели платят цену из таблицы ниже."}
          </div>
        )}

        {open && (
          <div className="space-y-3 border-t border-border-subtle pt-3">
            <div className="grid gap-3 md:grid-cols-3">
              <Input
                label="Процент скидки"
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                inputMode="numeric"
                error={percentError}
                trailing="%"
                hint={percentError ? undefined : "от 1 до 99"}
              />
              <div className="md:col-span-2">
                <Input
                  label="Подпись, которую увидит покупатель"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={200}
                  placeholder="Летняя акция"
                  hint="Пусто — бот покажет скидку без объяснения причины"
                />
              </div>
            </div>

            <div className="max-w-sm">
              <label className="text-xs font-medium text-fg-muted" htmlFor="discount-until">
                Действует до
              </label>
              <input
                id="discount-until"
                type="datetime-local"
                value={until}
                onChange={(e) => setUntil(e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-border-control bg-bg-card px-2.5 text-base text-fg"
              />
              <div className={untilError ? "mt-1 text-xs text-danger" : "mt-1 text-xs text-fg-subtle"}>
                {untilError ?? "Пусто — скидка бессрочная, пока не выключите вручную"}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button onClick={() => setOpen(false)}>Отмена</Button>
              <Button
                variant="primary"
                disabled={!canSubmit}
                onClick={() => setConfirm("save")}
              >
                Показать, что изменится
              </Button>
            </div>
          </div>
        )}
      </CardBody>

      <ConfirmDialog
        open={confirm === "save"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => save.mutate()}
        loading={save.isPending}
        title={`Скидка −${percentValid ? parsed : 0}% на все тарифы`}
        confirmLabel={`Включить −${percentValid ? parsed : 0}%`}
        cancelLabel="Не включать"
        body={
          <PriceDiff
            changes={percentValid ? changesFor(parsed) : []}
            note={
              <>
                Действует на всех новых покупателей сразу.{" "}
                {until
                  ? `Выключится сама ${fmtDate(new Date(until).toISOString())}.`
                  : "Выключается только вручную."}{" "}
                Комбо-тарифов не касается — их цена считается отдельно.
              </>
            }
          />
        }
      />

      <ConfirmDialog
        open={confirm === "clear"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => clear.mutate()}
        loading={clear.isPending}
        title="Отключить скидку — цены вырастут"
        confirmLabel="Отключить скидку"
        cancelLabel="Оставить скидку"
        body={
          <PriceDiff
            changes={changesFor(0)}
            note="Покупатели увидят новые цены сразу. Уже оплаченные подписки не пересчитываются."
          />
        }
      />
    </Card>
  );
}

/** ISO с зоной → значение для datetime-local (локальное время без зоны). */
function isoToInput(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}
