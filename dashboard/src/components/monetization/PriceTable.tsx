import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";

import { ApiError, endpoints, type TariffPriceRow } from "@/lib/api";
import { fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  Input,
  StatusBadge,
} from "@/components/ui";
import { PriceDiff, applyDiscount, type PriceChange } from "./PriceDiff";
import { periodLabel, tariffLabel } from "./labels";

/**
 * Прайс: тариф → периоды → цена, с правкой по одной строке.
 *
 * ГЛАВНОЕ ЗДЕСЬ — НЕ ПОЛЕ ВВОДА, А ПОДТВЕРЖДЕНИЕ. Между «набрал число» и
 * «покупатели платят это число» стоит окно, которое показывает «было →
 * станет» в рублях по затронутой паре тариф+период. Уберёте окно —
 * останется поле, в котором опечатка неотличима от намерения.
 *
 * ПОКАЗЫВАЕТСЯ ЭФФЕКТИВНАЯ ЦЕНА, А НЕ БАЗА. База — то, что лежит в
 * настройке; эффективная — то, что человек заплатит после глобальной
 * скидки. Правится база, сравнивается результат.
 *
 * КОМБО СЮДА НЕ ПОПАДАЕТ. Строки с editable=false отрисовывает
 * ComboPrices ниже по экрану: их цена живёт в config.COMBO_TARIFFS и
 * этими маршрутами не меняется. Поле ввода на них было бы обманом —
 * сохранялось бы и ни на что не влияло.
 */
export function PriceTable({
  rows,
  discountPercent,
}: {
  rows: TariffPriceRow[];
  /** Действующая глобальная скидка. Нужна, чтобы посчитать, что
   *  получится из новой базы. */
  discountPercent: number;
}) {
  const groups = useMemo(() => {
    const m = new Map<string, TariffPriceRow[]>();
    for (const r of rows) {
      const g = m.get(r.tariff) ?? [];
      g.push(r);
      m.set(r.tariff, g);
    }
    for (const arr of m.values()) arr.sort((a, b) => a.period_days - b.period_days);
    return Array.from(m.entries());
  }, [rows]);

  return (
    <div className="space-y-3">
      {groups.map(([tariff, list]) => (
        <Card key={tariff}>
          <CardHeader
            title={tariffLabel(tariff)}
            subtitle={`${list.length} ${list.length === 1 ? "период" : "периода(ов)"} · цену правит только эта таблица`}
          />
          <div className="divide-y divide-border-subtle">
            {list.map((row) => (
              <PriceRow
                key={row.period_days}
                row={row}
                discountPercent={discountPercent}
              />
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

/** Одна пара «тариф + период»: показ, правка, подтверждение. */
function PriceRow({
  row,
  discountPercent,
}: {
  row: TariffPriceRow;
  discountPercent: number;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(row.base_price));
  const [confirm, setConfirm] = useState<"save" | "reset" | null>(null);

  const parsed = Number(draft.trim());
  const valid = Number.isInteger(parsed) && parsed > 0 && parsed <= 10_000_000;
  const error = draft.trim() === "" || valid
    ? undefined
    : "Целое число рублей от 1 до 10 000 000";

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pricing"] });
  };

  const save = useMutation({
    mutationFn: () => endpoints.pricingSetOverride(row.tariff, row.period_days, parsed),
    onSuccess: () => {
      toast.success(
        `${tariffLabel(row.tariff)}, ${periodLabel(row.period_days)}: цена ${fmtRub(parsed)}`,
      );
      setConfirm(null);
      setEditing(false);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось сохранить цену"),
  });

  const reset = useMutation({
    mutationFn: () => endpoints.pricingClearOverride(row.tariff, row.period_days),
    onSuccess: () => {
      toast.success(
        `${tariffLabel(row.tariff)}, ${periodLabel(row.period_days)}: вернулась цена ${fmtRub(row.config_price)}`,
      );
      setConfirm(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось снять переопределение"),
  });

  // Что увидит покупатель после сохранения. Скидка применяется поверх
  // новой базы — ровно так же, как это делает сервер.
  const nextEffective = valid ? applyDiscount(parsed, discountPercent) : row.effective_price;
  const saveChange: PriceChange[] = [
    {
      tariff: row.tariff,
      periodDays: row.period_days,
      from: row.effective_price,
      to: nextEffective,
    },
  ];
  const resetChange: PriceChange[] = [
    {
      tariff: row.tariff,
      periodDays: row.period_days,
      from: row.effective_price,
      to: applyDiscount(row.config_price, discountPercent),
    },
  ];

  // Больше чем вдвое в любую сторону — характерный след опечатки
  // (лишний ноль, потерянный ноль). Не запрещаем, но говорим вслух.
  const ratio = row.effective_price > 0 ? nextEffective / row.effective_price : 1;
  const suspicious = valid && (ratio >= 2 || ratio <= 0.5);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
      <div className="min-w-[110px] shrink-0 text-base text-fg-muted">
        {periodLabel(row.period_days)}
      </div>

      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-1">
        {row.has_discount ? (
          <>
            <span className="text-base tabular-nums text-fg-subtle line-through">
              {fmtRub(row.base_price)}
            </span>
            <span className="text-lg font-semibold tabular-nums text-fg">
              {fmtRub(row.effective_price)}
            </span>
            <StatusBadge kind="info">скидка −{row.discount_percent}%</StatusBadge>
          </>
        ) : (
          <span className="text-lg font-semibold tabular-nums text-fg">
            {fmtRub(row.base_price)}
          </span>
        )}
        {row.is_overridden && (
          <span className="text-xs text-fg-muted">
            задано вручную · в конфиге {fmtRub(row.config_price)}
          </span>
        )}
      </div>

      {!editing ? (
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            onClick={() => {
              setDraft(String(row.base_price));
              setEditing(true);
            }}
          >
            Изменить
          </Button>
          {row.is_overridden && (
            <Button
              size="sm"
              variant="ghost"
              icon={<RotateCcw className="h-3 w-3" aria-hidden />}
              onClick={() => setConfirm("reset")}
            >
              Вернуть из конфига
            </Button>
          )}
        </div>
      ) : (
        <div className="flex shrink-0 items-end gap-1.5">
          <div className="w-36">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              inputMode="numeric"
              autoFocus
              error={error}
              trailing="₽"
              aria-label={`Цена: ${tariffLabel(row.tariff)}, ${periodLabel(row.period_days)}`}
            />
          </div>
          <Button
            size="sm"
            variant="primary"
            disabled={!valid || parsed === row.base_price}
            onClick={() => setConfirm("save")}
          >
            Сохранить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Отмена
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirm === "save"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => save.mutate()}
        loading={save.isPending}
        title={`Цена: ${tariffLabel(row.tariff)}, ${periodLabel(row.period_days)}`}
        confirmLabel={`Поставить ${fmtRub(nextEffective)}`}
        cancelLabel="Не менять"
        body={
          <PriceDiff
            changes={saveChange}
            note={
              <>
                Действует на всех новых покупателей сразу, кэш цен обновляется
                за полминуты. Уже оплаченные подписки не пересчитываются.
                {suspicious && (
                  <div className="mt-1 font-medium text-risk">
                    Разница больше чем вдвое. Проверьте разряды: {fmtRub(row.effective_price)} →{" "}
                    {fmtRub(nextEffective)}.
                  </div>
                )}
              </>
            }
          />
        }
      />

      <ConfirmDialog
        open={confirm === "reset"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => reset.mutate()}
        loading={reset.isPending}
        title={`Вернуть цену из конфига: ${tariffLabel(row.tariff)}, ${periodLabel(row.period_days)}`}
        confirmLabel={`Вернуть ${fmtRub(applyDiscount(row.config_price, discountPercent))}`}
        cancelLabel="Оставить как есть"
        body={
          <PriceDiff
            changes={resetChange}
            note="Снятие ручной цены — это тоже изменение цены, а не отмена правки: покупатели увидят другое число сразу."
          />
        }
      />
    </div>
  );
}

/**
 * Комбо-тарифы: показываем, но не даём править.
 *
 * ЗАЧЕМ ПОКАЗЫВАТЬ ТО, ЧТО НЕЛЬЗЯ ИЗМЕНИТЬ. Комбо — отдельный продаваемый
 * продукт со своей ценой, а не разновидность «Плюс». Прайс, в котором его
 * нет, врёт молчанием: человек правит четыре цены Плюса и уверен, что
 * закрыл прайс целиком, а комбо продолжает продаваться по старой.
 *
 * ГЛОБАЛЬНАЯ СКИДКА НА КОМБО ТОЖЕ НЕ ДЕЙСТВУЕТ: расчёт цены комбо идёт
 * мимо app/services/pricing (base_price_override_rubles в
 * database/subscription_pricing.py). Это написано прямо на экране —
 * иначе после включения скидки «на все тарифы» комбо молча останется в
 * старой цене.
 */
export function ComboPrices({ rows }: { rows: TariffPriceRow[] }) {
  const groups = useMemo(() => {
    const m = new Map<string, TariffPriceRow[]>();
    for (const r of rows) {
      const g = m.get(r.tariff) ?? [];
      g.push(r);
      m.set(r.tariff, g);
    }
    for (const arr of m.values()) arr.sort((a, b) => a.period_days - b.period_days);
    return Array.from(m.entries());
  }, [rows]);

  if (groups.length === 0) return null;

  return (
    <Card>
      <CardHeader
        title="Комбо-тарифы"
        subtitle="подписка вместе с пакетом ГБ обхода · цена задана в конфиге и из панели не меняется"
      />
      <div className="space-y-3 p-4">
        <p className="text-base text-fg-muted">
          Комбо — отдельный продукт, а не вариант «Плюс»: у него своя цена и
          свой состав. Ни ручная цена, ни глобальная скидка на него не
          действуют — расчёт идёт другим путём. Менять эти числа сейчас можно
          только в <code className="font-mono text-xs">config.COMBO_TARIFFS</code>.
        </p>
        {groups.map(([tariff, list]) => (
          <div key={tariff}>
            <div className="mb-1 text-base font-medium text-fg">{tariffLabel(tariff)}</div>
            <ul className="divide-y divide-border-subtle rounded-md border border-border">
              {list.map((r) => (
                <li
                  key={r.period_days}
                  className="flex items-baseline justify-between gap-3 px-2.5 py-1.5"
                >
                  <span className="text-base text-fg-muted">{periodLabel(r.period_days)}</span>
                  <span className="flex items-baseline gap-2">
                    {r.bypass_gb ? (
                      <span className="text-xs text-fg-subtle">{r.bypass_gb} ГБ обхода</span>
                    ) : null}
                    <span className="text-base font-semibold tabular-nums text-fg">
                      {fmtRub(r.base_price)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}
