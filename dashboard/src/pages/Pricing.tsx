import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Info,
  Percent,
  RefreshCcw,
  RotateCcw,
  Save,
  Tag,
  X,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtRub } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { toast } from "@/store/toast";

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
  biz_starter: "🏢 Business — Starter",
  biz_team: "🏢 Business — Team",
  biz_business: "🏢 Business — Business",
  biz_pro: "🏢 Business — Pro",
  biz_enterprise: "🏢 Business — Enterprise",
  biz_ultimate: "🏢 Business — Ultimate",
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
    <div className="mx-auto max-w-5xl space-y-4 pb-8 pt-2 md:pt-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-fg">
            <Tag className="h-5 w-5 text-fg-muted" />
            Цены и скидки
          </h1>
          <p className="mt-1 max-w-xl text-sm text-fg-muted">
            Управление ценами на тарифы и глобальной скидкой для всех
            пользователей. Изменения применяются мгновенно (кэш 30с).
          </p>
        </div>
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
      </header>

      {/* Global discount panel */}
      <GlobalDiscountPanel data={discount.data} loading={discount.isLoading} />

      {/* Info-плашка */}
      <div className="rounded-xl border border-info/20 bg-info/[0.06] p-3 text-[12px] leading-relaxed text-fg-muted">
        <Info className="mr-1 inline h-3.5 w-3.5 align-[-2px] text-info" />
        <b>Как это работает.</b>{" "}
        <b>Base price</b> берётся из override (если задан) или из{" "}
        <code>config.TARIFFS</code>.{" "}
        <b>Effective price</b> = base × (100 − скидка%), округление до рубля.
        В боте показывается зачёркнутая base + жирная effective + подпись
        причины.
      </div>

      {tariffs.isLoading ? (
        <div className="card flex items-center gap-2 p-6 text-sm text-fg-muted">
          <Spinner /> Загружаю тарифы…
        </div>
      ) : tariffs.isError ? (
        <div className="card p-4 text-sm text-danger">
          Не удалось загрузить: {(tariffs.error as ApiError)?.detail ?? "ошибка"}
        </div>
      ) : (
        <div className="space-y-3">
          {Array.from(grouped.entries()).map(([tariff, rows]) => (
            <TariffCard key={tariff} tariff={tariff} rows={rows} />
          ))}
        </div>
      )}
    </div>
  );
}

function TariffCard({ tariff, rows }: { tariff: string; rows: TariffRow[] }) {
  return (
    <section className="rounded-2xl border border-border bg-bg-card p-4">
      <h2 className="mb-2 text-sm font-semibold text-fg">
        {TARIFF_LABEL[tariff] ?? tariff}
      </h2>
      <div className="divide-y divide-border">
        {rows.map((r) => (
          <PriceRow key={r.period_days} row={r} />
        ))}
      </div>
    </section>
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
    <div className="flex flex-wrap items-center gap-3 py-3">
      <div className="min-w-[110px] shrink-0 text-sm text-fg-muted">
        {PERIOD_LABEL(row.period_days)}
      </div>

      <div className="flex flex-1 flex-wrap items-baseline gap-2">
        {row.has_discount ? (
          <>
            <span className="text-fg-subtle line-through tabular-nums">
              {fmtRub(row.base_price)}
            </span>
            <span className="text-base font-semibold tabular-nums text-success">
              {fmtRub(row.effective_price)}
            </span>
            <span className="rounded-md bg-success/10 px-1.5 py-0.5 text-[10px] font-semibold text-success">
              −{row.discount_percent}%
            </span>
          </>
        ) : (
          <span className="text-base font-semibold tabular-nums text-fg">
            {fmtRub(row.base_price)}
          </span>
        )}
        {row.is_overridden && (
          <span
            className="rounded-md bg-warning/15 px-1.5 py-0.5 text-[10px] font-semibold text-warning"
            title={`Base из config: ${fmtRub(row.config_price)}`}
          >
            OVERRIDE
          </span>
        )}
      </div>

      {!editing ? (
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setValue(String(row.base_price));
              setEditing(true);
            }}
            className="btn-ghost text-xs"
          >
            Изменить
          </button>
          {row.is_overridden && (
            <button
              type="button"
              onClick={() => clear.mutate()}
              disabled={clear.isPending}
              className="btn-ghost text-xs text-danger hover:text-danger"
              title="Убрать override — вернётся цена из config.TARIFFS"
            >
              <RotateCcw className="h-3 w-3" /> Сброс
            </button>
          )}
        </div>
      ) : (
        <div className="flex shrink-0 items-center gap-1">
          <input
            type="number"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input w-24 py-1 text-sm tabular-nums"
            autoFocus
            min={1}
          />
          <span className="text-xs text-fg-muted">₽</span>
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="btn-primary py-1 text-xs"
          >
            {save.isPending ? <Spinner /> : <Save className="h-3 w-3" />}
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="btn-ghost py-1 text-xs"
          >
            <X className="h-3 w-3" />
          </button>
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
    <section
      className={
        active
          ? "rounded-2xl border border-success/30 bg-gradient-to-br from-success/10 to-white p-4"
          : "rounded-2xl border border-border bg-bg-card p-4"
      }
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
            <Percent className="h-3 w-3" /> Глобальная скидка
          </div>
          {loading ? (
            <div className="mt-1 text-sm text-fg-muted">Загружаю…</div>
          ) : active ? (
            <>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums text-success">
                  −{data!.global_discount_percent}%
                </span>
                <span className="text-sm text-fg-muted">на все тарифы</span>
              </div>
              {data?.discount_reason && (
                <div className="mt-0.5 text-sm text-fg">
                  «{data.discount_reason}»
                </div>
              )}
              {data?.discount_until_at && (
                <div className="mt-0.5 text-[11px] text-fg-subtle">
                  Действует до{" "}
                  <b className="text-fg-muted">
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
            <div className="mt-1 text-sm text-fg-muted">Не активна</div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {active && (
            <button
              type="button"
              onClick={() => {
                if (confirm("Отключить глобальную скидку?")) clear.mutate();
              }}
              className="btn-ghost text-danger hover:text-danger"
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
          >
            {active ? "Изменить" : "Включить скидку"}
          </button>
        </div>
      </div>

      {showForm && (
        <div className="mt-3 border-t border-border/60 pt-3">
          <div className="grid gap-3 md:grid-cols-3">
            <label className="block">
              <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
                Процент скидки (1–99)
              </div>
              <input
                type="number"
                min={1}
                max={99}
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                className="input"
              />
            </label>
            <label className="block md:col-span-2">
              <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
                Причина / подпись (видит юзер)
              </div>
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
              <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
                Действует до (опционально)
              </div>
              <input
                type="datetime-local"
                value={until}
                onChange={(e) => setUntil(e.target.value)}
                className="input"
              />
              <div className="mt-1 text-[10px] text-fg-subtle">
                Пусто — бессрочная скидка, пока не отключишь вручную.
              </div>
            </label>
          </div>
          <div className="mt-3 flex justify-end gap-2">
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
    </section>
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
