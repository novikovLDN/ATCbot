import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCcw, RotateCcw, Save, X } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtRub } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { toast } from "@/store/toast";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton, StatusDot } from "@/components/ui/controls";
import { ErrorState, LoadingTiles, Skeleton } from "@/components/ui/states";

interface TariffRow {
  tariff: string;
  period_days: number;
  base_price: number;
  config_price: number;
  effective_price: number;
  discount_percent: number;
  is_overridden: boolean;
  has_discount: boolean;
}

const TARIFF_LABEL: Record<string, string> = {
  basic: "🏆 Basic",
  plus: "💎 Plus",
};

const PERIOD_LABEL = (d: number): string => {
  if (d === 30) return "1 месяц";
  if (d === 90) return "3 месяца";
  if (d === 180) return "6 месяцев";
  if (d === 365) return "1 год";
  if (d === 730) return "2 года";
  return `${d} дней`;
};

export function Pricing() {
  const tariffs = useQuery({
    queryKey: ["pricing", "tariffs"],
    queryFn: () => endpoints.pricingTariffs(),
    refetchInterval: 60_000,
  });
  const discount = useQuery({
    queryKey: ["pricing", "global-discount"],
    queryFn: () => endpoints.pricingGetGlobalDiscount(),
    refetchInterval: 60_000,
  });

  const grouped = useMemo(() => {
    const m = new Map<string, TariffRow[]>();
    for (const r of tariffs.data ?? []) {
      const g = m.get(r.tariff) ?? [];
      g.push(r);
      m.set(r.tariff, g);
    }
    for (const arr of m.values())
      arr.sort((a, b) => a.period_days - b.period_days);
    return m;
  }, [tariffs.data]);

  return (
    <>
      <PageHeader
        title="Цены и скидки"
        sub="Управление ценами на тарифы и глобальной скидкой для всех пользователей. Изменения применяются мгновенно (кэш 30с)."
        actions={
          <button
            type="button"
            onClick={() => {
              tariffs.refetch();
              discount.refetch();
            }}
            className="btn-secondary"
            disabled={tariffs.isFetching || discount.isFetching}
          >
            {tariffs.isFetching || discount.isFetching ? (
              <Spinner />
            ) : (
              <RefreshCcw className="h-3.5 w-3.5" />
            )}
            Обновить
          </button>
        }
      />

      <Bento>
        <GlobalDiscountPanel data={discount.data} loading={discount.isLoading} />

        <Surface className="sm:col-span-6 xl:col-span-4" variant="fog" label="Как это работает">
          <p className="text-[13px] leading-5">
            <b>Base price</b> берётся из override (если задан) или из{" "}
            <code>config.TARIFFS</code>.{" "}
            <b>Effective price</b> = base × (100 − скидка%), округление до рубля.
            В боте показывается зачёркнутая base + жирная effective + подпись
            причины.
          </p>
        </Surface>

        {tariffs.isLoading ? (
          <div className="sm:col-span-6 xl:col-span-12">
            <LoadingTiles count={4} />
          </div>
        ) : tariffs.isError ? (
          <ErrorState
            className="sm:col-span-6 xl:col-span-12"
            error={tariffs.error}
            onRetry={() => tariffs.refetch()}
          />
        ) : (
          Array.from(grouped.entries()).map(([tariff, rows], i) => (
            <TariffCard key={tariff} tariff={tariff} rows={rows} raised={i % 2 === 1} />
          ))
        )}
      </Bento>
    </>
  );
}

function TariffCard({ tariff, rows, raised }: { tariff: string; rows: TariffRow[]; raised: boolean }) {
  return (
    <Surface
      className="sm:col-span-6 xl:col-span-6"
      variant={raised ? "raised" : "ink"}
      label={TARIFF_LABEL[tariff] ?? tariff}
    >
      <ul className="flex flex-col gap-2">
        {rows.map((r) => (
          <li key={r.period_days}>
            <PriceRow row={r} />
          </li>
        ))}
      </ul>
    </Surface>
  );
}

function PriceRow({ row }: { row: TariffRow }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<string>(String(row.base_price));

  const save = useMutation({
    mutationFn: () => {
      const v = parseInt(value, 10);
      if (!Number.isFinite(v) || v <= 0) {
        throw new Error("Цена должна быть > 0");
      }
      return endpoints.pricingSetOverride(row.tariff, row.period_days, v);
    },
    onSuccess: () => {
      toast.success("Цена сохранена");
      qc.invalidateQueries({ queryKey: ["pricing", "tariffs"] });
      setEditing(false);
    },
    onError: (e: unknown) => {
      const msg =
        (e as ApiError)?.detail ??
        (e instanceof Error ? e.message : "Ошибка");
      toast.error(msg);
    },
  });

  const clear = useMutation({
    mutationFn: () =>
      endpoints.pricingClearOverride(row.tariff, row.period_days),
    onSuccess: () => {
      toast.success("Override снят — вернулась цена из config");
      qc.invalidateQueries({ queryKey: ["pricing", "tariffs"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <div className="list-row flex-wrap">
      <div className="t-body min-w-[96px] flex-none text-[13px]">
        {PERIOD_LABEL(row.period_days)}
      </div>

      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-2">
        {row.has_discount ? (
          <>
            <span className="t-mute tabular text-[13px] line-through">
              {fmtRub(row.base_price)}
            </span>
            <span className="tabular text-[15px] font-semibold">
              {fmtRub(row.effective_price)}
            </span>
            <span className="badge-success tabular">−{row.discount_percent}%</span>
          </>
        ) : (
          <span className="tabular text-[15px] font-semibold">
            {fmtRub(row.base_price)}
          </span>
        )}
        {row.is_overridden && (
          <span
            className="badge-warning"
            title={`Base из config: ${fmtRub(row.config_price)}`}
          >
            override
          </span>
        )}
      </div>

      {!editing ? (
        <div className="flex flex-none items-center gap-1">
          <button
            type="button"
            onClick={() => {
              setValue(String(row.base_price));
              setEditing(true);
            }}
            className="btn-ghost"
          >
            Изменить
          </button>
          {row.is_overridden && (
            <button
              type="button"
              onClick={() => clear.mutate()}
              disabled={clear.isPending}
              className="btn-ghost text-danger hover:text-danger"
              title="Убрать override — вернётся цена из config.TARIFFS"
            >
              <RotateCcw className="h-3.5 w-3.5" /> Сброс
            </button>
          )}
        </div>
      ) : (
        <div className="flex flex-none items-center gap-2">
          <input
            type="number"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input tabular w-28 bg-tile-1"
            aria-label={`Цена, ${PERIOD_LABEL(row.period_days)}`}
            autoFocus
            min={1}
          />
          <span className="t-mute text-[13px]">₽</span>
          <IconButton
            label="Сохранить"
            accent
            onClick={() => save.mutate()}
            disabled={save.isPending}
          >
            {save.isPending ? <Spinner /> : <Save className="h-4 w-4" />}
          </IconButton>
          <IconButton label="Отмена" onClick={() => setEditing(false)}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>
      )}
    </div>
  );
}

function GlobalDiscountPanel({
  data,
  loading,
}: {
  data:
    | {
        global_discount_percent: number;
        discount_reason: string | null;
        discount_until_at: string | null;
      }
    | undefined;
  loading: boolean;
}) {
  const qc = useQueryClient();
  const active =
    data && data.global_discount_percent > 0 && !isExpired(data.discount_until_at);

  const [showForm, setShowForm] = useState(false);
  const [percent, setPercent] = useState<string>(
    data?.global_discount_percent ? String(data.global_discount_percent) : "10",
  );
  const [reason, setReason] = useState<string>(data?.discount_reason ?? "");
  const [until, setUntil] = useState<string>(
    data?.discount_until_at ? isoToInput(data.discount_until_at) : "",
  );

  const save = useMutation({
    mutationFn: () => {
      const p = parseInt(percent, 10);
      if (!Number.isFinite(p) || p < 1 || p > 99) {
        throw new Error("Скидка от 1 до 99%");
      }
      return endpoints.pricingSetGlobalDiscount({
        percent: p,
        reason: reason.trim() || null,
        until_at_iso: until ? inputToIso(until) : null,
      });
    },
    onSuccess: () => {
      toast.success("Скидка обновлена");
      qc.invalidateQueries({ queryKey: ["pricing"] });
      setShowForm(false);
    },
    onError: (e: unknown) => {
      const msg =
        (e as ApiError)?.detail ??
        (e instanceof Error ? e.message : "Ошибка");
      toast.error(msg);
    },
  });

  const clear = useMutation({
    mutationFn: () => endpoints.pricingClearGlobalDiscount(),
    onSuccess: () => {
      toast.success("Скидка отключена");
      qc.invalidateQueries({ queryKey: ["pricing"] });
      setShowForm(false);
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <Surface
      className="sm:col-span-6 xl:col-span-8"
      variant={active ? "raised" : "ink"}
      label="Глобальная скидка"
      aside={
        loading ? null : active ? (
          <span className="badge-success">
            <StatusDot tone="ok" /> Активна
          </span>
        ) : (
          <span className="badge-muted">Не активна</span>
        )
      }
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          {loading ? (
            <Skeleton className="h-9 w-28" />
          ) : active ? (
            <>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="tabular text-[30px] font-semibold leading-9">
                  −{data!.global_discount_percent}%
                </span>
                <span className="t-mute text-[13px]">на все тарифы</span>
              </div>
              {data?.discount_reason && (
                <div className="mt-1 text-[14px]">«{data.discount_reason}»</div>
              )}
              {data?.discount_until_at && (
                <div className="t-mute mt-1 text-[12px]">
                  Действует до{" "}
                  <b className="t-body tabular">
                    {new Date(data.discount_until_at).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </b>{" "}
                  МСК
                </div>
              )}
            </>
          ) : (
            <p className="t-mute text-[14px]">Не активна</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {active && (
            <button
              type="button"
              onClick={() => {
                if (confirm("Отключить глобальную скидку?")) clear.mutate();
              }}
              className="btn-danger"
              disabled={clear.isPending}
            >
              {clear.isPending ? <Spinner /> : <X className="h-3.5 w-3.5" />}
              Отключить
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowForm((v) => !v)}
            className="btn-primary"
            aria-expanded={showForm}
          >
            {active ? "Изменить" : "Включить скидку"}
          </button>
        </div>
      </div>

      {showForm && (
        <div className="mt-5">
          <div className="grid gap-3 md:grid-cols-3">
            <label className="block">
              <span className="t-mute mb-1.5 block text-[13px]">Процент скидки (1–99)</span>
              <input
                type="number"
                min={1}
                max={99}
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                className="input tabular"
              />
            </label>
            <label className="block md:col-span-2">
              <span className="t-mute mb-1.5 block text-[13px]">Причина / подпись (видит юзер)</span>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Летняя акция"
                maxLength={200}
                className="input"
              />
            </label>
            <label className="block md:col-span-3">
              <span className="t-mute mb-1.5 block text-[13px]">Действует до (опционально)</span>
              <input
                type="datetime-local"
                value={until}
                onChange={(e) => setUntil(e.target.value)}
                className="input"
              />
              <span className="t-mute mt-1 block text-[12px]">
                Пусто — бессрочная скидка, пока не отключишь вручную.
              </span>
            </label>
          </div>
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="btn-secondary"
              disabled={save.isPending}
            >
              Отмена
            </button>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="btn-primary"
            >
              {save.isPending ? <Spinner /> : <Check className="h-3.5 w-3.5" />}
              Сохранить
            </button>
          </div>
        </div>
      )}
    </Surface>
  );
}

function isExpired(iso: string | null | undefined): boolean {
  if (!iso) return false;
  try {
    return new Date(iso).getTime() < Date.now();
  } catch {
    return false;
  }
}

function isoToInput(iso: string): string {
  // ISO с TZ → YYYY-MM-DDTHH:MM (для datetime-local, локальное время)
  try {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return "";
  }
}

function inputToIso(local: string): string {
  // datetime-local (без TZ) → ISO с TZ (текущий)
  try {
    const d = new Date(local);
    return d.toISOString();
  } catch {
    return "";
  }
}
