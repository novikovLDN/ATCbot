/**
 * «Сверка» — reconciliation section at the bottom of Dashboard.
 *
 * Shows users whose PREMIUM subscription expires more than 8 years from now,
 * plus a stream of recent auto-detected over-issuance events. Each candidate
 * expands to a detail card with:
 *   • current vs. expected expires_at (delta in days/years),
 *   • all approved payments used as proof (counted / uncounted),
 *   • «Исправить» button that recomputes expires_at from the sum of
 *     counted payments + admin_grant_days.
 *
 * The over-issuance events include a python stack snippet (caller_context)
 * so we can trace WHERE the excessive duration came from.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";

const fmtDate = (iso: string | null) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
};

const fmtDays = (days: number) => {
  if (days < 0) return `${days} д`;
  if (days < 365) return `${days} д`;
  const years = (days / 365).toFixed(1);
  return `${years} лет · ${days} д`;
};

export function ReconciliationSection() {
  const candidates = useQuery({
    queryKey: ["reconciliation", "candidates"],
    queryFn: endpoints.reconciliationCandidates,
    // Not every navigation, but often enough to reflect fresh state.
    refetchInterval: 60_000,
  });
  const overIssuance = useQuery({
    queryKey: ["reconciliation", "over-issuance"],
    queryFn: endpoints.reconciliationOverIssuanceLog,
    refetchInterval: 60_000,
  });

  return (
    <section className="animate-fade-up rounded-2xl border border-border bg-bg-card p-5 md:p-6">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
            <ShieldCheck className="h-3.5 w-3.5 text-accent" />
            Сверка подписок
          </div>
          <h2 className="mt-1.5 text-lg font-semibold text-fg md:text-xl">
            Аномалии в expires_at &gt; 8 лет
          </h2>
          <p className="mt-1 text-xs text-fg-muted">
            Пользователи с премиумом дольше 8 лет — сверяем со суммой
            оплаченных периодов и админ-выдач. Нажми «Исправить», чтобы
            подрезать expires_at до фактически купленного срока.
          </p>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
            Кандидатов
          </div>
          <div className="mt-0.5 text-2xl font-semibold text-fg tabular-nums md:text-3xl">
            {candidates.isLoading ? "—" : fmtNum(candidates.data?.total ?? 0)}
          </div>
        </div>
      </header>

      {candidates.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю список…
        </div>
      ) : candidates.isError ? (
        <div className="rounded-xl border border-danger/30 bg-danger/10 p-3 text-sm text-danger">
          Ошибка загрузки: {(candidates.error as ApiError)?.detail ?? "…"}
        </div>
      ) : candidates.data?.items[0]?.panel_unreachable ? (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-warning">
          Не удалось получить список пользователей из Remnawave.
          Проверь REMNAWAVE_API_URL / TOKEN и логи —
          <code className="ml-1 font-mono">get_all_users</code> вернул None.
        </div>
      ) : (candidates.data?.items.length ?? 0) === 0 ? (
        <div className="rounded-xl border border-border bg-bg-subtle/40 p-6 text-center text-sm text-fg-muted">
          Кандидатов нет — все премиум-энтити в Remnawave укладываются
          в 8-летний коридор.
        </div>
      ) : (
        <div className="space-y-2.5">
          {candidates.data!.items.map((c) => (
            <CandidateRow key={c.telegram_id} row={c} />
          ))}
        </div>
      )}

      {/* Auto-detected events stream (watchdog output) */}
      <div className="mt-6 border-t border-border pt-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
            Авто-детект новых выдач &gt; 8 лет
          </div>
          <div className="text-[10px] text-fg-subtle">
            обновляется каждые 60с
          </div>
        </div>
        {overIssuance.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-fg-muted">
            <Spinner /> …
          </div>
        ) : (overIssuance.data?.length ?? 0) === 0 ? (
          <div className="text-xs text-fg-subtle">
            Событий не зафиксировано.
          </div>
        ) : (
          <div className="space-y-1.5">
            {overIssuance.data!.slice(0, 8).map((e) => (
              <OverIssuanceRow key={e.id} row={e} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// ── One candidate — expandable detail card ─────────────────────────

type CandidateRow = NonNullable<
  Awaited<ReturnType<typeof endpoints.reconciliationCandidates>>
>["items"][number];

function CandidateRow({ row }: { row: CandidateRow }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-border bg-bg-subtle/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left transition-colors hover:bg-bg-elevated/60"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <div className="font-mono text-sm text-fg">{row.telegram_id}</div>
            {row.username && (
              <div className="truncate text-xs text-fg-subtle">
                @{row.username}
              </div>
            )}
            {row.db_row_missing && (
              <span className="rounded-full bg-warning/15 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-warning">
                нет строки в DB
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            <span>{row.subscription_type ?? "?"}</span>
            <span className="text-fg-subtle">·</span>
            <span>source: {row.source ?? "?"}</span>
            {row.admin_grant_days ? (
              <>
                <span className="text-fg-subtle">·</span>
                <span>admin_grant: {row.admin_grant_days} д</span>
              </>
            ) : null}
            {row.panel_username && (
              <>
                <span className="text-fg-subtle">·</span>
                <span className="font-mono text-[10px]">
                  {row.panel_username}
                </span>
              </>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
            в панели Remnawave
          </div>
          <div className="mt-0.5 font-semibold tabular-nums text-warning">
            {row.years_from_now} лет
          </div>
          <div className="text-[10px] text-fg-subtle">
            {fmtDate(row.panel_expires_at ?? row.expires_at)}
            {!row.panel_available && (
              <span className="ml-1 text-fg-subtle">
                (панель недоступна · показан DB)
              </span>
            )}
          </div>
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 text-fg-subtle" />
        ) : (
          <ChevronDown className="h-4 w-4 text-fg-subtle" />
        )}
      </button>

      {open && <CandidateDetail telegram_id={row.telegram_id} />}
    </div>
  );
}

function CandidateDetail({ telegram_id }: { telegram_id: number }) {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["reconciliation", "detail", telegram_id],
    queryFn: () => endpoints.reconciliationDetail(telegram_id),
  });
  const fix = useMutation({
    mutationFn: () => endpoints.reconciliationFix(telegram_id),
    onSuccess: (data) => {
      toast.success(
        `Исправлено: снято ${fmtDays(data.days_removed)} по ${data.proof_payment_ids.length} платежам`,
      );
      qc.invalidateQueries({ queryKey: ["reconciliation"] });
    },
    onError: (e: unknown) => {
      const err = e as ApiError;
      if (err.status === 409) {
        toast.error(
          "Не могу подрезать: расчёт даёт срок ДЛИННЕЕ текущего. Проверь платежи вручную.",
        );
      } else {
        toast.error(err.detail ?? "Не удалось применить фикс");
      }
    },
  });

  if (detail.isLoading) {
    return (
      <div className="border-t border-border px-4 py-3 text-xs text-fg-muted">
        <Spinner /> Загружаю платежи…
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="border-t border-border px-4 py-3 text-xs text-danger">
        Не удалось загрузить детали.
      </div>
    );
  }

  const d = detail.data;
  const delta = d.delta_days;
  const wouldExtend = d.expected_days_from_now > d.actual_days_from_now;

  return (
    <div className="space-y-3 border-t border-border px-4 py-4 text-xs">
      {/* Delta summary — 5 tiles: DB + panel + paid + admin + expected */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Metric label="DB expires_at" value={fmtDays(d.actual_days_from_now)} />
        <Metric
          label="панель Remnawave"
          value={
            d.panel.available
              ? d.panel.days_from_now !== null
                ? fmtDays(d.panel.days_from_now)
                : "—"
              : "недоступна"
          }
          tone={
            d.panel.available && d.panel.days_from_now !== null && d.panel.days_from_now > 8 * 365
              ? "danger"
              : "muted"
          }
        />
        <Metric
          label="Оплачено (счит.)"
          value={`${fmtNum(d.total_paid_days)} д`}
        />
        <Metric
          label="Admin grant"
          value={`${fmtNum(d.subscription.admin_grant_days)} д`}
        />
        <Metric
          label="Ожидаемо"
          value={fmtDays(d.expected_days_from_now)}
          tone={delta > 0 ? "danger" : "muted"}
        />
      </div>

      {/* Panel mismatch hint — DB vs Remnawave */}
      {d.panel.available && !d.panel.matches_db && (
        <div className="rounded-lg border border-accent/30 bg-accent/10 p-3 text-fg">
          <div className="font-semibold text-accent">
            DB и панель расходятся
          </div>
          <div className="mt-1 text-fg-muted">
            В bot-DB: <span className="font-mono">{fmtDate(d.subscription.expires_at)}</span>
            {" · "}
            В Remnawave (<span className="font-mono">tg_{telegram_id}_premium</span>):{" "}
            <span className="font-mono">{fmtDate(d.panel.expires_at)}</span>.
            «Исправить» подрежет expires_at в bot-DB (Remnawave не трогаем — там уже корректный срок).
          </div>
        </div>
      )}

      {/* Reason */}
      <div className="rounded-lg border border-warning/30 bg-warning/10 p-3">
        <div className="font-semibold text-warning">Причина к правке</div>
        <div className="mt-1 text-fg">
          {delta > 0 ? (
            <>
              Разница <b>+{fmtDays(delta)}</b> относительно суммы платежей и
              админ-грантов. Ожидаемый expires_at:{" "}
              <span className="font-mono">
                {fmtDate(d.expected_expires_at)}
              </span>
              .
            </>
          ) : (
            <>Расчёт даёт срок не длиннее текущего — правка не требуется.</>
          )}
        </div>
      </div>

      {/* Payments proof */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
            Платежи ({d.payments.length})
          </div>
          <a
            href={`/dashboard/users?telegram_id=${telegram_id}`}
            className="inline-flex items-center gap-1 text-fg-muted hover:text-accent"
          >
            <ExternalLink className="h-3 w-3" />
            открыть карточку
          </a>
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead className="bg-bg-elevated text-[10px] uppercase tracking-wider text-fg-subtle">
              <tr>
                <th className="px-2 py-1.5 text-left">id</th>
                <th className="px-2 py-1.5 text-left">tariff</th>
                <th className="px-2 py-1.5 text-right">₽</th>
                <th className="px-2 py-1.5 text-right">дни</th>
                <th className="px-2 py-1.5 text-left">paid_at</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {d.payments.map((p) => (
                <tr
                  key={p.id}
                  className={p.counted ? "" : "text-fg-subtle line-through"}
                >
                  <td className="px-2 py-1 font-mono">{p.id}</td>
                  <td className="px-2 py-1 font-mono">{p.tariff}</td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {p.amount_rubles.toFixed(0)}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {p.period_days ?? "—"}
                  </td>
                  <td className="px-2 py-1">{fmtDate(p.paid_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Over-issuance events for this user */}
      {d.over_issuance_events.length > 0 && (
        <details className="rounded-lg border border-border bg-bg-elevated/40 p-2">
          <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wider text-fg-muted">
            События auto-детекта ({d.over_issuance_events.length})
          </summary>
          <div className="mt-2 space-y-1.5">
            {d.over_issuance_events.map((e) => (
              <div
                key={e.id}
                className="rounded border border-border bg-bg-card p-2 font-mono text-[10px]"
              >
                <div className="flex items-center gap-2 text-fg">
                  <span>{fmtDate(e.created_at)}</span>
                  <span className="text-fg-subtle">·</span>
                  <span>action: {e.grant_action ?? "?"}</span>
                  <span className="text-fg-subtle">·</span>
                  <span>source: {e.source ?? "?"}</span>
                  {e.tariff && (
                    <>
                      <span className="text-fg-subtle">·</span>
                      <span>tariff: {e.tariff}</span>
                    </>
                  )}
                </div>
                {e.caller_context && (
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-fg-muted">
                    {e.caller_context}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Fix button */}
      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          className="btn-primary"
          disabled={fix.isPending || wouldExtend}
          onClick={() => {
            if (
              window.confirm(
                `Подрезать expires_at у ${telegram_id} до ${fmtDate(d.expected_expires_at)}?\n\n` +
                  `Снимется ~${fmtDays(delta)}.\n` +
                  `Доказательство: ${d.payments.filter((p) => p.counted).length} платежей + ${d.subscription.admin_grant_days} дней admin_grant.`,
              )
            ) {
              fix.mutate();
            }
          }}
        >
          {fix.isPending ? <Spinner /> : <Wrench className="h-3.5 w-3.5" />}
          Исправить
        </button>
        {wouldExtend && (
          <span className="text-fg-subtle">
            Правка не требуется — expected ≥ actual.
          </span>
        )}
      </div>
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────

function Metric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "danger" | "muted";
}) {
  const toneCls =
    tone === "danger" ? "text-danger" : tone === "muted" ? "text-fg-muted" : "text-fg";
  return (
    <div className="rounded-lg border border-border bg-bg-card p-2.5">
      <div className="text-[9px] uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div
        className={`mt-0.5 font-semibold tabular-nums ${toneCls}`}
      >
        {value}
      </div>
    </div>
  );
}

type OverIssuanceRow = Awaited<
  ReturnType<typeof endpoints.reconciliationOverIssuanceLog>
>[number];

function OverIssuanceRow({ row }: { row: OverIssuanceRow }) {
  const days = row.duration_added_seconds
    ? Math.round(row.duration_added_seconds / 86400)
    : null;
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-bg-subtle/30 px-2.5 py-1.5 text-[11px]">
      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
      <div className="min-w-0 flex-1">
        <div className="font-mono text-fg">
          {row.telegram_id}
          <span className="ml-2 text-fg-subtle">·</span>
          <span className="ml-2 text-fg-muted">
            {row.grant_action ?? "?"} / {row.source ?? "?"}
            {row.tariff ? ` / ${row.tariff}` : ""}
          </span>
          {days !== null && (
            <>
              <span className="ml-2 text-fg-subtle">·</span>
              <span className="ml-2 text-warning">+{fmtNum(days)} д</span>
            </>
          )}
        </div>
        <div className="text-[10px] text-fg-subtle">
          {fmtDate(row.created_at)}
        </div>
      </div>
    </div>
  );
}
