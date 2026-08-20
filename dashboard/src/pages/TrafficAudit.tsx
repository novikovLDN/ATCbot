import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Database,
  Gauge,
  Loader2,
  RefreshCw,
  Search,
  Wrench,
  XCircle,
} from "lucide-react";
import { endpoints, ApiError } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";

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
  no_entity: "Нет в панели",
  panel_error: "Ошибка API",
} as const;

const KIND_STYLE = {
  match: "text-success bg-success/10 ring-success/25",
  mismatch: "text-warning bg-warning/10 ring-warning/25",
  no_entity: "text-fg-muted bg-bg-elevated ring-border",
  panel_error: "text-danger bg-danger/10 ring-danger/25",
} as const;

type Kind = keyof typeof KIND_LABEL;
type Row = Awaited<ReturnType<typeof endpoints.trafficAuditList>>["results"][number];

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

  const [confirmAll, setConfirmAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [kindFilter, setKindFilter] = useState<Kind | "all">("mismatch");

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
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
            Maintenance
          </div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-fg md:text-4xl">
            Traffic Audit — DB ↔ Панель
          </h1>
          <p className="mt-2 max-w-3xl text-sm text-fg-muted">
            Сравнение оплаченного трафика (
            <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono text-xs">
              subscription_base + Σ traffic_purchases
            </code>
            ) с фактическим лимитом в панели Remnawave (
            <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono text-xs">
              trafficLimitBytes
            </code>
            ). Показывает где у юзера в БД, например, 85 ГБ, а в панели — 0.
            Fix поднимает лимит до{" "}
            <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono text-xs">
              expected + used
            </code>
            {" "}(usedTrafficBytes сохраняется, remaining = ровно expected).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-fg-muted">
            <span>Скан:</span>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              disabled={q.isFetching}
              className="rounded-md border border-border bg-bg-elevated px-2 py-1 text-xs text-fg"
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
            className="btn-secondary text-xs"
          >
            {q.isFetching ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Прогнать аудит
          </button>
        </div>
      </header>

      {/* Summary */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <SummaryCard
          label="Проверено"
          value={fmtNum(summary?.total ?? 0)}
          icon={Database}
          tone="muted"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Совпадают"
          value={fmtNum(summary?.match ?? 0)}
          icon={CheckCircle2}
          tone="success"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Расхождения"
          value={fmtNum(summary?.mismatch ?? 0)}
          icon={AlertTriangle}
          tone="warning"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Нет в панели"
          value={fmtNum(summary?.no_entity ?? 0)}
          icon={XCircle}
          tone="muted"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Недодача, ГБ"
          value={fmtGb(summary?.shortfall_total_gb ?? 0)}
          icon={Gauge}
          tone="warning"
          loading={q.isLoading}
        />
      </section>

      {/* One-user query */}
      <section className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-xs font-medium text-fg-muted">
            Разовая проверка по telegram_id:
          </div>
          <input
            type="text"
            inputMode="numeric"
            placeholder="8343902286"
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && scanOne()}
            className="w-52 rounded-md border border-border bg-bg-elevated px-3 py-1.5 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={scanOne}
            disabled={oneUser.isPending}
            className="btn-secondary text-xs"
          >
            {oneUser.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            Проверить одного
          </button>
        </div>
        {oneUser.data && oneUser.data.results.length > 0 && (
          <div className="mt-3 space-y-2">
            {oneUser.data.results.map((r) => (
              <ResultRow
                key={`one-${r.tg}`}
                r={r}
                expanded={true}
                onToggle={() => {}}
                onFix={() => fixOne.mutate(r.tg)}
                fixing={fixOne.isPending && fixOne.variables === r.tg}
              />
            ))}
          </div>
        )}
        {oneUser.data && oneUser.data.results.length === 0 && (
          <div className="mt-3 text-xs text-fg-subtle">
            Юзер не найден в БД (или нет bypass entity).
          </div>
        )}
      </section>

      {/* Filter + fix-all */}
      <section className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <Filter value={kindFilter} onChange={setKindFilter} summary={summary} />
        <div className="flex items-center gap-2">
          {!confirmAll ? (
            <button
              type="button"
              onClick={() => setConfirmAll(true)}
              disabled={(summary?.mismatch ?? 0) === 0 || fixAll.isPending}
              className="btn-primary"
            >
              <Wrench className="h-3.5 w-3.5" />
              Починить все ({fmtNum(summary?.mismatch ?? 0)})
            </button>
          ) : (
            <>
              <span className="text-xs text-fg-muted">
                PATCH на {fmtNum(summary?.mismatch ?? 0)} юзеров в панели?
              </span>
              <button
                type="button"
                onClick={() => setConfirmAll(false)}
                className="btn-secondary text-xs"
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
                className="btn-danger text-xs"
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
      </section>

      {/* Table */}
      {q.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-24" />
          ))}
        </div>
      ) : q.isError ? (
        <div className="card border-danger/30 bg-danger/5 p-4 text-sm text-danger">
          Не удалось прогнать audit. Попробуй уменьшить лимит и повторить.
        </div>
      ) : filtered.length === 0 ? (
        <div className="card grid place-items-center gap-2 p-12 text-center">
          <CheckCircle2 className="h-10 w-10 text-success" />
          <div className="text-base font-medium text-fg">
            {(summary?.mismatch ?? 0) === 0 && kindFilter === "mismatch"
              ? "Расхождений нет — все юзеры совпадают."
              : "Под фильтр никто не попал."}
          </div>
          {kindFilter !== "all" && (
            <button
              type="button"
              onClick={() => setKindFilter("all")}
              className="text-xs text-accent underline"
            >
              Показать всех ({summary?.total ?? 0})
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
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
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─ Components ────────────────────────────────────────────────────────

function SummaryCard({
  label,
  value,
  icon: Icon,
  tone,
  loading,
}: {
  label: string;
  value: string;
  icon: typeof AlertTriangle;
  tone: "warning" | "success" | "muted";
  loading?: boolean;
}) {
  const toneClass =
    tone === "warning"
      ? "text-warning bg-warning/10 ring-warning/30"
      : tone === "success"
      ? "text-success bg-success/10 ring-success/25"
      : "text-fg-muted bg-bg-subtle ring-border";
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
            {label}
          </div>
          <div className="mt-1 truncate text-xl font-semibold tabular-nums text-fg md:text-2xl">
            {loading ? "…" : value}
          </div>
        </div>
        <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ring-1 ${toneClass}`}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </div>
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
    no_entity: number;
    panel_error: number;
  };
}) {
  const opts: Array<{ key: Kind | "all"; label: string; count: number }> = [
    { key: "mismatch", label: "Расхождения", count: summary?.mismatch ?? 0 },
    { key: "panel_error", label: "Ошибки API", count: summary?.panel_error ?? 0 },
    { key: "no_entity", label: "Нет в панели", count: summary?.no_entity ?? 0 },
    { key: "match", label: "OK", count: summary?.match ?? 0 },
    { key: "all", label: "Все", count: summary?.total ?? 0 },
  ];
  return (
    <div className="inline-flex flex-wrap rounded-full border border-border bg-bg-elevated p-0.5 text-xs font-medium">
      {opts.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={
            "rounded-full px-3 py-1.5 transition-colors " +
            (value === o.key
              ? "bg-accent font-semibold text-bg shadow-glow-sm"
              : "text-fg-muted hover:text-fg")
          }
        >
          {o.label} · <span className="tabular-nums">{fmtNum(o.count)}</span>
        </button>
      ))}
    </div>
  );
}

function ResultRow({
  r,
  expanded,
  onToggle,
  onFix,
  fixing,
}: {
  r: Row;
  expanded: boolean;
  onToggle: () => void;
  onFix: () => void;
  fixing: boolean;
}) {
  const canFix = r.kind === "mismatch" && r.shortfall_bytes > 0;
  const tariffBadge = r.is_bypass_only
    ? "bypass-only"
    : `${r.subscription_type}${r.period_days ? ` · ${r.period_days}d` : ""}`;

  return (
    <article className="card overflow-hidden p-0">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-fg-subtle" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
        )}
        <span
          className={`badge shrink-0 ring-1 ${KIND_STYLE[r.kind]}`}
          title={r.kind}
        >
          {KIND_LABEL[r.kind]}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm tabular-nums text-fg">
              tg:{r.tg}
            </span>
            <span className="badge bg-bg-elevated text-fg-muted ring-1 ring-border">
              {tariffBadge}
            </span>
            {r.traffic_purchases_gb > 0 && (
              <span className="badge bg-tagpurple/15 text-tagpurple ring-1 ring-tagpurple/25">
                +{fmtNum(r.traffic_purchases_gb)} ГБ пакетов
              </span>
            )}
            {r.panel_status && r.panel_status !== "—" && (
              <span className="badge bg-bg-elevated text-fg-subtle ring-1 ring-border text-[10px]">
                {r.panel_status}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-fg-muted">
            <span>
              <b>DB:</b> {fmtGb(r.expected_gb)}
            </span>
            <span>·</span>
            <span>
              <b>Панель:</b> {fmtGb(r.actual_gb)}
            </span>
            <span>·</span>
            <span>
              <b>Used:</b> {fmtGb(r.used_gb)}
            </span>
            {r.shortfall_gb > 0 && (
              <>
                <span>·</span>
                <span className="font-semibold text-warning">
                  Δ {fmtGb(r.shortfall_gb)}
                </span>
              </>
            )}
          </div>
        </div>
        {canFix && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onFix();
            }}
            disabled={fixing}
            className="btn-primary shrink-0 text-xs"
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
        <div className="border-t border-border bg-bg-subtle/30 p-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <DetailCard title="В нашей БД" tone="muted">
              <KV label="subscription" value={tariffBadge} />
              <KV
                label="traffic packs"
                value={r.traffic_purchases_gb > 0 ? `+${r.traffic_purchases_gb} ГБ` : "—"}
              />
              <KV label="ожидаемый лимит" value={fmtGb(r.expected_gb)} />
            </DetailCard>
            <DetailCard title="В панели Remnawave" tone={r.kind === "mismatch" ? "warning" : "muted"}>
              <KV label="trafficLimitBytes" value={fmtGb(r.actual_gb)} />
              <KV label="usedTrafficBytes" value={fmtGb(r.used_gb)} />
              <KV label="status" value={r.panel_status} />
            </DetailCard>
            <DetailCard
              title={canFix ? "Что применит fix" : "Разница"}
              tone={canFix ? "success" : "muted"}
            >
              {canFix ? (
                <>
                  <KV
                    label="new_limit"
                    value={fmtGb((r.expected_gb ?? 0) + (r.used_gb ?? 0))}
                  />
                  <KV
                    label="формула"
                    value="expected + used"
                  />
                  <KV
                    label="Δ добавит"
                    value={`+${fmtGb(r.shortfall_gb)}`}
                  />
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
        </div>
      )}
    </article>
  );
}

function DetailCard({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "warning" | "success" | "muted";
  children: React.ReactNode;
}) {
  const cls =
    tone === "warning"
      ? "border-warning/30 bg-warning/8"
      : tone === "success"
      ? "border-success/30 bg-success/8"
      : "border-border bg-bg-subtle/50";
  return (
    <div className={`rounded-xl border ${cls} p-3`}>
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {title}
      </div>
      <dl className="mt-2 space-y-1.5">{children}</dl>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="truncate font-mono text-fg">{value}</dd>
    </div>
  );
}
