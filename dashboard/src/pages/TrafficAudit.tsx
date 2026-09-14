import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, RefreshCw, Search, Wrench } from "lucide-react";
import { endpoints, ApiError, type PanelEntitySnapshot } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

// Traffic Audit — сравнение DB (subscription base + Σ traffic_purchases)
// vs Remnawave panel (trafficLimitBytes). Ловим mismatches: юзер оплатил
// 85 ГБ, а в панели 0 — нужно поднять trafficLimitBytes = expected + used.

const fmtGb = (gb: number | null | undefined): string => {
  if (gb == null || Number.isNaN(gb)) return "—";
  if (gb === 0) return "0";
  if (gb < 0.01) return "<0.01 ГБ";
  if (gb < 1) return `${gb.toFixed(2)} ГБ`;
  return `${gb.toFixed(gb >= 100 ? 0 : 1)} ГБ`;
};

const KIND_LABEL = {
  match: "OK",
  mismatch: "Расхождение",
  desync: "РАССИНХРОН",
  no_entity: "Нет в панели",
  panel_error: "Ошибка API",
} as const;

const KIND_STYLE = {
  match: "badge-success",
  mismatch: "badge-warning",
  desync: "badge-danger font-semibold",
  no_entity: "badge bg-tile-1 text-mute",
  panel_error: "badge-danger",
} as const;

type Kind = keyof typeof KIND_LABEL;
type Row = Awaited<ReturnType<typeof endpoints.trafficAuditList>>["results"][number];

const INLINE_CODE = "font-mono text-[12px] on-shell";

export function TrafficAudit() {
  const qc = useQueryClient();
  const [limit, setLimit] = useState<number>(200);
  const [userFilter, setUserFilter] = useState<string>("");
  const [scanKey, setScanKey] = useState<number>(0);

  const q = useQuery({
    queryKey: ["traffic-audit", limit, scanKey],
    queryFn: () => endpoints.trafficAuditList({ limit }),
    // Долгая операция → не пересчитывать автоматом
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  const oneUser = useMutation({
    mutationFn: (tg: number) => endpoints.trafficAuditList({ user: tg }),
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось загрузить"),
  });

  const fixOne = useMutation({
    mutationFn: (tg: number) => endpoints.trafficAuditFixOne(tg),
    onSuccess: (data) => {
      const delta =
        (data.after_bytes ?? 0) - (data.before_bytes ?? 0);
      toast.success(
        `Юзер починен: +${(delta / 1024 ** 3).toFixed(2)} ГБ`,
      );
      qc.invalidateQueries({ queryKey: ["traffic-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Fix не прошёл"),
  });

  const fixAll = useMutation({
    mutationFn: () => endpoints.trafficAuditFixAll({ concurrent: 3 }),
    onSuccess: (data) => {
      toast.success(
        `Готово: починено ${data.fixed} из ${data.audit_summary.mismatch}, ошибок ${data.failed}`,
      );
      qc.invalidateQueries({ queryKey: ["traffic-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Массовый fix упал"),
  });

  const resyncOne = useMutation({
    mutationFn: (tg: number) => endpoints.trafficAuditResync(tg),
    onSuccess: (data) => {
      toast.success(
        `Пересинк: id=${data.new_id ?? "—"}, лимит ${(
          (data.panel_limit_bytes || 0) / 1024 ** 3
        ).toFixed(1)} ГБ`,
      );
      qc.invalidateQueries({ queryKey: ["traffic-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Resync упал"),
  });

  const resetPremiumUnlim = useMutation({
    mutationFn: (dry: boolean) => endpoints.remnawaveResetPremiumUnlimited(dry),
    onSuccess: (data) => {
      if (data.dry_run) {
        toast.success(
          `Dry-run: нашли ${data.limited} premium с лимитом (из ${data.total}). Нажми ещё раз "Применить" чтобы сбросить.`,
        );
      } else {
        toast.success(
          `Сброшено: ${data.reset} premium → безлимит (было ограничено: ${data.limited})`,
        );
      }
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Reset упал"),
  });
  const [confirmResetPrem, setConfirmResetPrem] = useState(false);

  const [confirmAll, setConfirmAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [kindFilter, setKindFilter] = useState<Kind | "all">("desync");

  const summary = q.data?.summary;
  const filtered = useMemo(() => {
    const all = q.data?.results ?? [];
    return kindFilter === "all" ? all : all.filter((r) => r.kind === kindFilter);
  }, [q.data, kindFilter]);

  const scanOne = () => {
    const tg = Number(userFilter.trim());
    if (!Number.isFinite(tg) || tg <= 0) {
      toast.error("Введите telegram_id (число > 0)");
      return;
    }
    oneUser.mutate(tg);
  };

  return (
    <>
      <PageHeader
        title="Traffic Audit — DB ↔ Панель"
        sub={
          <>
            Сравнение оплаченного трафика (<code className={INLINE_CODE}>subscription_base + Σ traffic_purchases</code>)
            с фактическим лимитом в панели Remnawave (<code className={INLINE_CODE}>trafficLimitBytes</code>).
            Показывает где у юзера в БД, например, 85 ГБ, а в панели — 0. Fix поднимает лимит до{" "}
            <code className={INLINE_CODE}>expected + used</code> (usedTrafficBytes сохраняется, remaining = ровно
            expected).
          </>
        }
        actions={
          <>
            <label className="on-shell-mute flex items-center gap-2 text-[13px]">
              <span>Скан:</span>
              <select
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                disabled={q.isFetching}
                className="input tabular w-auto"
              >
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
                <option value={1000}>1000</option>
                <option value={5000}>5000</option>
              </select>
            </label>
            <button
              type="button"
              onClick={() => {
                setScanKey((k) => k + 1);
                q.refetch();
              }}
              disabled={q.isFetching}
              className="btn-secondary"
            >
              {q.isFetching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Прогнать аудит
            </button>
          </>
        }
      />

      <Bento>
        {/* Summary */}
        <SummaryTile label="Проверено" value={fmtNum(summary?.total ?? 0)} loading={q.isLoading} />
        <SummaryTile label="Совпадают" value={fmtNum(summary?.match ?? 0)} tone="ok" loading={q.isLoading} variant="raised" />
        <SummaryTile label="Рассинхрон" value={fmtNum(summary?.desync ?? 0)} tone="err" loading={q.isLoading} variant="steel" />
        <SummaryTile label="Расхождения" value={fmtNum(summary?.mismatch ?? 0)} tone="warn" loading={q.isLoading} variant="raised" />
        <SummaryTile label="Нет в панели" value={fmtNum(summary?.no_entity ?? 0)} tone="idle" loading={q.isLoading} variant="raised" />
        <SummaryTile label="Недодача, ГБ" value={fmtGb(summary?.shortfall_total_gb ?? 0)} tone="warn" loading={q.isLoading} variant="fog" />

        {/* Emergency: reset all premium entities to unlimited */}
        <Surface className="sm:col-span-6 xl:col-span-12" variant="raised">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 max-w-[72ch]">
              <h2 className="flex items-center gap-2 text-[15px] font-semibold">
                <StatusDot tone="err" />
                Сбросить ВСЕ premium → безлимит
              </h2>
              <p className="t-mute mt-1 text-[13px] leading-5">
                По ТЗ premium — без лимита ГБ. Если бот случайно PATCH-нул trafficLimitBytes на premium (баг), они
                уходят в LIMITED. Кнопка ставит trafficLimitBytes=0 + status=ACTIVE для всех premium entities разом.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {!confirmResetPrem ? (
                <>
                  <button
                    type="button"
                    onClick={() => resetPremiumUnlim.mutate(true)}
                    disabled={resetPremiumUnlim.isPending}
                    className="btn-secondary"
                  >
                    {resetPremiumUnlim.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    Dry-run
                  </button>
                  <button type="button" onClick={() => setConfirmResetPrem(true)} className="btn-danger">
                    Применить ко всем
                  </button>
                </>
              ) : (
                <>
                  <span className="t-body text-[13px]">Точно PATCH всех premium → limit=0?</span>
                  <button
                    type="button"
                    onClick={() => setConfirmResetPrem(false)}
                    className="btn-secondary"
                    disabled={resetPremiumUnlim.isPending}
                  >
                    Отмена
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      resetPremiumUnlim.mutate(false, {
                        onSettled: () => setConfirmResetPrem(false),
                      });
                    }}
                    disabled={resetPremiumUnlim.isPending}
                    className="btn-danger"
                  >
                    {resetPremiumUnlim.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    Да, применить
                  </button>
                </>
              )}
            </div>
          </div>
        </Surface>

        {/* One-user query */}
        <Surface className="sm:col-span-6 xl:col-span-12" label="Разовая проверка по telegram_id">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              inputMode="numeric"
              placeholder="8343902286"
              aria-label="telegram_id"
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && scanOne()}
              className="input tabular w-full sm:w-56"
            />
            <button type="button" onClick={scanOne} disabled={oneUser.isPending} className="btn-secondary">
              {oneUser.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Search className="h-3.5 w-3.5" />
              )}
              Проверить одного
            </button>
          </div>
          {oneUser.data && oneUser.data.results.length > 0 && (
            <div className="mt-4 flex flex-col gap-2">
              {oneUser.data.results.map((r) => (
                <ResultRow
                  key={`one-${r.tg}`}
                  r={r}
                  expanded={true}
                  onToggle={() => {}}
                  onFix={() => fixOne.mutate(r.tg)}
                  fixing={fixOne.isPending && fixOne.variables === r.tg}
                  onResync={() => resyncOne.mutate(r.tg)}
                  resyncing={resyncOne.isPending && resyncOne.variables === r.tg}
                />
              ))}
            </div>
          )}
          {oneUser.data && oneUser.data.results.length === 0 && (
            <p className="t-mute mt-3 text-[13px]">Юзер не найден в БД (или нет bypass entity).</p>
          )}
        </Surface>

        {/* Filter + fix-all + list */}
        <Surface className="sm:col-span-6 xl:col-span-12" label="Результаты">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="scrollbar-none -mx-1 max-w-full overflow-x-auto px-1">
              <Filter value={kindFilter} onChange={setKindFilter} summary={summary} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {!confirmAll ? (
                <button
                  type="button"
                  onClick={() => setConfirmAll(true)}
                  disabled={(summary?.mismatch ?? 0) === 0 || fixAll.isPending}
                  className="btn-primary"
                >
                  <Wrench className="h-3.5 w-3.5" />
                  Починить все (<span className="tabular">{fmtNum(summary?.mismatch ?? 0)}</span>)
                </button>
              ) : (
                <>
                  <span className="t-body text-[13px]">
                    PATCH на {fmtNum(summary?.mismatch ?? 0)} юзеров в панели?
                  </span>
                  <button
                    type="button"
                    onClick={() => setConfirmAll(false)}
                    className="btn-secondary"
                    disabled={fixAll.isPending}
                  >
                    Отмена
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      fixAll.mutate(undefined, {
                        onSettled: () => setConfirmAll(false),
                      });
                    }}
                    disabled={fixAll.isPending}
                    className="btn-danger"
                  >
                    {fixAll.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Wrench className="h-3.5 w-3.5" />
                    )}
                    Да, применить
                  </button>
                </>
              )}
            </div>
          </div>

          {q.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-24 rounded-row" />
              ))}
            </div>
          ) : q.isError ? (
            <div className="flex flex-col gap-2">
              <ErrorState className="rounded-row bg-tile-3 p-4" error={q.error} onRetry={() => q.refetch()} />
              <p className="t-mute px-1 text-[13px]">Попробуй уменьшить лимит скана до 50 и повторить.</p>
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              title={
                (summary?.mismatch ?? 0) === 0 && kindFilter === "mismatch"
                  ? "Расхождений нет — все юзеры совпадают."
                  : "Под фильтр никто не попал."
              }
              action={
                kindFilter !== "all" ? (
                  <button type="button" onClick={() => setKindFilter("all")} className="btn-secondary mt-1">
                    Показать всех ({summary?.total ?? 0})
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div className="flex flex-col gap-2">
              {filtered.map((r) => (
                <ResultRow
                  key={r.tg}
                  r={r}
                  expanded={expanded.has(r.tg)}
                  onToggle={() =>
                    setExpanded((prev) => {
                      const n = new Set(prev);
                      if (n.has(r.tg)) n.delete(r.tg);
                      else n.add(r.tg);
                      return n;
                    })
                  }
                  onFix={() => fixOne.mutate(r.tg)}
                  fixing={fixOne.isPending && fixOne.variables === r.tg}
                  onResync={() => resyncOne.mutate(r.tg)}
                  resyncing={resyncOne.isPending && resyncOne.variables === r.tg}
                />
              ))}
            </div>
          )}
        </Surface>
      </Bento>
    </>
  );
}

// ─ Components ────────────────────────────────────────────────────────

function SummaryTile({
  label,
  value,
  tone,
  loading,
  variant,
}: {
  label: string;
  value: string;
  tone?: Tone;
  loading?: boolean;
  variant?: "ink" | "raised" | "steel" | "fog";
}) {
  return (
    <KpiTile
      className="sm:col-span-2 xl:col-span-2"
      size="sm"
      variant={variant}
      label={label}
      loading={loading}
      value={
        tone ? (
          <span className="inline-flex items-center gap-2.5">
            <StatusDot tone={tone} />
            <span className="truncate">{value}</span>
          </span>
        ) : (
          value
        )
      }
    />
  );
}

function Filter({
  value,
  onChange,
  summary,
}: {
  value: Kind | "all";
  onChange: (v: Kind | "all") => void;
  summary?: {
    total: number;
    match: number;
    mismatch: number;
    desync: number;
    no_entity: number;
    panel_error: number;
  };
}) {
  const opts: Array<{ key: Kind | "all"; label: string; count: number }> = [
    { key: "desync", label: "Рассинхрон", count: summary?.desync ?? 0 },
    { key: "mismatch", label: "Расхождения", count: summary?.mismatch ?? 0 },
    { key: "panel_error", label: "Ошибки API", count: summary?.panel_error ?? 0 },
    { key: "no_entity", label: "Нет в панели", count: summary?.no_entity ?? 0 },
    { key: "match", label: "OK", count: summary?.match ?? 0 },
    { key: "all", label: "Все", count: summary?.total ?? 0 },
  ];
  return (
    <Segmented
      label="Фильтр по статусу"
      value={value}
      onChange={onChange}
      options={opts.map((o) => ({ value: o.key, label: `${o.label} · ${fmtNum(o.count)}` }))}
    />
  );
}

function ResultRow({
  r,
  expanded,
  onToggle,
  onFix,
  fixing,
  onResync,
  resyncing,
}: {
  r: Row;
  expanded: boolean;
  onToggle: () => void;
  onFix: () => void;
  fixing: boolean;
  onResync: () => void;
  resyncing: boolean;
}) {
  const canFix = r.kind === "mismatch" && r.shortfall_bytes > 0;
  const canResync = r.kind === "desync";
  const tariffBadge = r.is_bypass_only
    ? "bypass-only"
    : `${r.subscription_type}${r.period_days ? ` · ${r.period_days}d` : ""}`;

  return (
    <article className="overflow-hidden rounded-row bg-tile-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-center gap-3 px-3 py-3 text-left transition-colors hover:bg-tile-4 sm:flex-nowrap"
      >
        {expanded ? (
          <ChevronDown className="t-mute h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="t-mute h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <span className={cn("shrink-0", KIND_STYLE[r.kind])} title={r.kind}>
          {KIND_LABEL[r.kind]}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="tabular font-mono text-[14px]">tg:{r.tg}</span>
            <span className="badge bg-tile-1 text-mute">{tariffBadge}</span>
            {r.traffic_purchases_gb > 0 && (
              <span className="badge-special">+{fmtNum(r.traffic_purchases_gb)} ГБ пакетов</span>
            )}
            {r.panel_status && r.panel_status !== "—" && (
              <span className="badge bg-tile-1 text-mute">{r.panel_status}</span>
            )}
          </div>
          <div className="t-mute tabular mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
            <span>
              <span className="t-body font-medium">DB:</span> {fmtGb(r.expected_gb)}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              <span className="t-body font-medium">Панель:</span> {fmtGb(r.actual_gb)}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              <span className="t-body font-medium">Used:</span> {fmtGb(r.used_gb)}
            </span>
            {r.shortfall_gb > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span className="font-semibold text-warning">Δ {fmtGb(r.shortfall_gb)}</span>
              </>
            )}
          </div>
        </div>
        {canResync && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onResync();
            }}
            disabled={resyncing}
            className="btn-danger shrink-0"
            title="Обновить remnawave_uuid/id в БД → указать на правильную entity"
          >
            {resyncing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Пересинк
          </button>
        )}
        {canFix && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onFix();
            }}
            disabled={fixing}
            className="btn-primary shrink-0"
          >
            {fixing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wrench className="h-3.5 w-3.5" />
            )}
            Починить
          </button>
        )}
      </button>

      {expanded && (
        <div className="flex flex-col gap-3 px-3 pb-3">
          {/* DESYNC-баннер + сравнение entity ─────────────────────────────── */}
          {r.kind === "desync" && (
            <div className="rounded-row bg-tile-1 p-4">
              <h3 className="flex items-center gap-2 text-[15px] font-semibold">
                <StatusDot tone="err" />
                РАССИНХРОН: бот показывает юзеру не ту entity
              </h3>
              <p className="t-body mt-2 text-[13px] leading-5">
                Бот резолвит одну entity через сохранённый в БД uuid/id, а под этим username в панели лежит другая
                entity с реальным трафиком. Юзер получает ссылку на "пустую" entity и видит "трафика нет", хотя
                купленный лимит есть — просто на другой entity.
              </p>
              <p className="t-mute mt-2 break-all font-mono text-[12px]">{r.note}</p>
              <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                <EntitySnapCard
                  title="Что бот сейчас показывает (по uuid из БД)"
                  snap={r.panel_by_our_ref}
                  tone="warn"
                />
                <EntitySnapCard
                  title="Что реально лежит в панели (по username)"
                  snap={r.panel_by_username}
                  tone="ok"
                />
              </div>
              <p className="t-mute mt-3 text-[13px] leading-5">
                Кнопка «Пересинк» перепишет{" "}
                <code className="rounded-full bg-tile-3 px-2 py-0.5 font-mono text-[12px]">
                  subscriptions.remnawave_{"{uuid,id,bypass_sub_url}"}
                </code>{" "}
                → указать на entity по username.
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <DetailCard title="В нашей БД">
              <KV label="subscription" value={tariffBadge} />
              <KV
                label="traffic packs"
                value={r.traffic_purchases_gb > 0 ? `+${r.traffic_purchases_gb} ГБ` : "—"}
              />
              <KV label="ожидаемый лимит" value={fmtGb(r.expected_gb)} />
            </DetailCard>
            <DetailCard title="В панели Remnawave" tone={r.kind === "mismatch" ? "warn" : undefined}>
              <KV label="trafficLimitBytes" value={fmtGb(r.actual_gb)} />
              <KV label="usedTrafficBytes" value={fmtGb(r.used_gb)} />
              <KV label="status" value={r.panel_status} />
            </DetailCard>
            <DetailCard title={canFix ? "Что применит fix" : "Разница"} tone={canFix ? "ok" : undefined}>
              {canFix ? (
                <>
                  <KV label="new_limit" value={fmtGb((r.expected_gb ?? 0) + (r.used_gb ?? 0))} />
                  <KV label="формула" value="expected + used" />
                  <KV label="Δ добавит" value={`+${fmtGb(r.shortfall_gb)}`} />
                </>
              ) : (
                <>
                  <KV label="shortfall" value={fmtGb(r.shortfall_gb)} />
                  <KV label="статус" value={KIND_LABEL[r.kind]} />
                  {r.note && <KV label="note" value={r.note} />}
                </>
              )}
            </DetailCard>
          </div>

          {/* Детализация покупок трафика — только в single-user mode
              (при разовой проверке), иначе массив пустой. Показывает
              откуда взялась сумма Σ traffic_purchases: реальные строки
              из БД (id / GB / RUB / метод / дата). */}
          {r.traffic_purchases && r.traffic_purchases.length > 0 && (
            <div className="rounded-row bg-tile-1 p-4">
              <h3 className="mb-3 text-[15px] font-semibold">
                История покупок трафика{" "}
                <span className="t-mute tabular text-[13px] font-normal">· {r.traffic_purchases.length} шт.</span>
              </h3>
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="dtable min-w-[520px]">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th className="num">ГБ</th>
                      <th className="num">₽</th>
                      <th>Метод</th>
                      <th className="num">id</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.traffic_purchases.map((tp) => (
                      <tr key={tp.id}>
                        <td className="tabular">
                          {tp.created_at
                            ? new Date(tp.created_at).toLocaleString("ru-RU", {
                                day: "2-digit",
                                month: "2-digit",
                                year: "2-digit",
                                hour: "2-digit",
                                minute: "2-digit",
                              })
                            : "—"}
                        </td>
                        <td className="num font-semibold">{fmtNum(tp.gb_amount)}</td>
                        <td className="num t-body">{fmtNum(tp.price_rub)}</td>
                        <td className="t-body">{tp.payment_method ?? "—"}</td>
                        <td className="num t-mute font-mono text-[12px]">{tp.id}</td>
                      </tr>
                    ))}
                    <tr>
                      <td className="t-mute font-semibold">Σ</td>
                      <td className="num font-semibold">{fmtNum(r.traffic_purchases_gb)}</td>
                      <td colSpan={3} />
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function BlockTitle({ tone, children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-[13px] font-semibold">
      {tone && <StatusDot tone={tone} />}
      {children}
    </div>
  );
}

function DetailCard({
  title,
  tone,
  children,
}: {
  title: string;
  tone?: Tone;
  children: ReactNode;
}) {
  return (
    <div className="rounded-row bg-tile-1 p-3">
      <BlockTitle tone={tone}>{title}</BlockTitle>
      <dl className="mt-2 flex flex-col gap-1.5">{children}</dl>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[13px]">
      <dt className="t-mute">{label}</dt>
      <dd className="tabular truncate font-mono text-[12px]">{value}</dd>
    </div>
  );
}

function EntitySnapCard({
  title,
  snap,
  tone,
}: {
  title: string;
  snap: PanelEntitySnapshot | null;
  tone: Tone;
}) {
  if (!snap) {
    return (
      <div className="rounded-row bg-tile-3 p-3">
        <BlockTitle tone={tone}>{title}</BlockTitle>
        <div className="t-mute mt-2 text-[13px] italic">не найдена</div>
      </div>
    );
  }
  const limitGb = snap.traffic_limit_bytes / 1024 ** 3;
  const usedGb = snap.used_traffic_bytes / 1024 ** 3;
  return (
    <div className="rounded-row bg-tile-3 p-3">
      <BlockTitle tone={tone}>{title}</BlockTitle>
      <dl className="mt-2 flex flex-col gap-1.5">
        <KV label="id" value={String(snap.panel_id ?? "—")} />
        <KV
          label="vlessUuid"
          value={snap.vless_uuid ? snap.vless_uuid.slice(0, 8) + "…" : "—"}
        />
        <KV
          label="лимит"
          value={
            snap.traffic_limit_bytes === 0
              ? "0 (∞)"
              : `${limitGb.toFixed(2)} ГБ`
          }
        />
        <KV label="used" value={`${usedGb.toFixed(2)} ГБ`} />
        <KV label="status" value={snap.status} />
        <KV
          label="telegramId"
          value={String(snap.telegram_id_field ?? "—")}
        />
      </dl>
      {snap.subscription_url && (
        <div className="t-mute mt-2 truncate font-mono text-[12px]" title={snap.subscription_url}>
          {snap.subscription_url}
        </div>
      )}
    </div>
  );
}
