/**
 * Health — is the machine working, top to bottom:
 *   1. System: database, Telegram webhook, panel, Redis, version, alerts, workers.
 *   2. Payments: providers, time to pay, stuck invoices, payment errors.
 *   3. Delivery: the provisioning queue, dead jobs (read-only retry help),
 *      activations, delivery errors.
 *   4. Tools: the Remnawave id backfill.
 * Each block reads its own endpoint, so one failing query never blanks the
 * screen. Replaces the old Operations screen. Every "?" reads lib/metricDefs.
 */
import { useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  PROVIDER_LABEL,
  STAGE_LABEL,
  WORKER_STATE_LABEL,
  metricsApi,
  type DeliveryHealth,
  type OperationsReport,
  type PaymentsHealth,
  type ProviderHealth,
  type ProviderPerf,
  type SystemHealth,
  type WorkerState,
} from "@/lib/metricsApi";
import { DEF } from "@/lib/metricDefs";
import { fmtDate, fmtDay, fmtDuration, fmtNum, fmtPct, fmtRelative, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Bento, PageHeader, SectionHeader, Surface } from "@/components/ui/Surface";
import { Hint } from "@/components/ui/Hint";
import { IconButton, ListRow, PillProgress, StatusDot, type Tone } from "@/components/ui/controls";
import { ReasonList, statusLabel, statusTone } from "@/components/ui/StatusBadge";
import { BarsChart } from "@/components/ui/charts";
import { EmptyState, ErrorState, LoadingTiles, Skeleton } from "@/components/ui/states";
import { RemnawaveBackfillCard } from "@/components/RemnawaveBackfillCard";
import { CopyButton } from "@/components/users/shared/CopyButton";

// ── Small helpers ────────────────────────────────────────────────────

const BIG = "tabular text-[30px] font-semibold leading-9";
const NESTED = "rounded-row bg-tile-3 p-3";

function providerLabel(p: string | null | undefined): string {
  if (!p) return "Все провайдеры";
  return PROVIDER_LABEL[p] ?? p;
}

function stageLabel(s: string): string {
  return STAGE_LABEL[s] ?? s;
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return `${fmtNum(ms < 10 ? Math.round(ms * 10) / 10 : Math.round(ms))} мс`;
}

/** Status word with a dot: "Работает", "Не отвечает"… Never colour alone. */
function StateLine({ tone, text, meta }: { tone: Tone; text: string; meta?: ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-2 text-[18px] font-semibold leading-6">
        <StatusDot tone={tone} />
        {text}
      </div>
      {meta && <p className="t-mute mt-1 break-words text-[13px] leading-5">{meta}</p>}
    </div>
  );
}

/** Label + value row inside a tile. */
function Fact({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[13px] leading-5">
      <span className="t-body inline-flex min-w-0 items-center gap-1.5">
        <span className="truncate">{label}</span>
        {hint && <Hint text={hint} />}
      </span>
      <span className="tabular min-w-0 break-words text-right font-medium">{value}</span>
    </div>
  );
}

/** A small figure in a nested block. */
function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className={NESTED}>
      <div className="tabular text-[22px] font-semibold leading-7">{value}</div>
      <p className="t-mute text-[12px] leading-4">{label}</p>
      {sub && <p className="t-mute mt-0.5 text-[12px] leading-4">{sub}</p>}
    </div>
  );
}

/** StatusBadge's vocabulary for text set on the device shell: the capsule
    tints itself from the tile colour, which reads wrong outside a tile. */
function ShellStatus({ status }: { status: Parameters<typeof statusTone>[0] }) {
  return (
    <span className="on-shell inline-flex items-center gap-1.5 text-[13px] font-medium">
      <StatusDot tone={statusTone(status)} />
      {statusLabel(status)}
    </span>
  );
}

function SectionError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return <ErrorState error={error} onRetry={onRetry} />;
}

// ── 1. System ────────────────────────────────────────────────────────

const WORKER_TONE: Record<WorkerState["state"], Tone> = {
  ok: "ok",
  failing: "err",
  stale: "warn",
  paused: "idle",
  starting: "info",
};
const WORKER_ORDER: Record<WorkerState["state"], number> = { failing: 0, stale: 1, starting: 2, paused: 3, ok: 4 };

const ALERT_CATEGORY: Record<string, string> = {
  payment: "Платежи",
  worker: "Воркеры",
  other: "Прочее",
};

function alertCategory(key: string): string {
  if (key.startsWith("push:")) {
    const rest = key.slice(5);
    return `Push: ${(ALERT_CATEGORY[rest] ?? rest).toLowerCase()}`;
  }
  return ALERT_CATEGORY[key] ?? key;
}

function WorkerRow({ w }: { w: WorkerState }) {
  const meta = [
    w.last_ok_age_s != null ? `последний цикл ${fmtDuration(w.last_ok_age_s)} назад` : "ещё не завершал цикл",
    `интервал ${fmtDuration(w.interval_s)}`,
    w.fail_count ? `ошибок ${fmtNum(w.fail_count)}${w.consecutive_fails ? `, подряд ${fmtNum(w.consecutive_fails)}` : ""}` : null,
    w.skip_count ? `пропущено циклов ${fmtNum(w.skip_count)}` : null,
  ].filter(Boolean);
  return (
    <div className={NESTED}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="min-w-0 break-words text-[14px] font-medium">{w.label || w.name}</span>
        <span className="inline-flex flex-none items-center gap-1.5 text-[12px]">
          <StatusDot tone={WORKER_TONE[w.state] ?? "idle"} />
          {WORKER_STATE_LABEL[w.state] ?? w.state}
        </span>
      </div>
      <p className="t-mute mt-0.5 text-[12px] leading-4">{meta.join(", ")}</p>
      {w.last_error && (
        <p className="t-body mt-1 break-words text-[12px] leading-4">
          Последняя ошибка{w.last_fail_at ? ` ${fmtRelative(w.last_fail_at)}` : ""}: {w.last_error}
        </p>
      )}
    </div>
  );
}

function SystemSection({ h }: { h: SystemHealth }) {
  const pool = h.db.pool;
  const poolPct = pool && pool.max > 0 ? (pool.in_use / pool.max) * 100 : null;
  const wh = h.webhook;
  const workers = [...h.workers].sort((a, b) => (WORKER_ORDER[a.state] ?? 9) - (WORKER_ORDER[b.state] ?? 9));
  const workersOk = h.workers.filter((w) => w.state === "ok").length;
  const alertsPartial = h.alerts.covered_s < h.alerts.window_s;
  const flagsOff = [
    h.flags.background_workers === false ? "Фоновые воркеры выключены флагом: очереди и напоминания не идут." : null,
    h.flags.auto_renewal === false ? "Автопродление выключено флагом: списаний с балансов не будет." : null,
  ].filter((x): x is string => Boolean(x));

  const dbState: { tone: Tone; text: string } = !h.db.ready
    ? { tone: "err", text: "Не готова" }
    : h.db.ok
      ? { tone: "ok", text: "Работает" }
      : { tone: "err", text: "Ошибка" };

  return (
    <Bento>
      <Surface className="sm:col-span-6 xl:col-span-6" label="Общий статус" hint={DEF.system_status}>
        <StateLine tone={statusTone(h.overall.status)} text={statusLabel(h.overall.status)} />
        <div className="mt-4">
          <ReasonList reasons={h.overall.reasons} empty="Все компоненты в норме" />
        </div>
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="raised" label="База данных" hint={DEF.db_pool}>
        <StateLine
          tone={dbState.tone}
          text={dbState.text}
          meta={h.db.error ? h.db.error : `SELECT 1 за ${fmtMs(h.db.latency_ms)}`}
        />
        <div className="mt-4">
          {pool ? (
            <>
              <PillProgress value={poolPct} label="Пул занят" valueLabel={`${fmtNum(pool.in_use)} / ${fmtNum(pool.max)}`} />
              <p className="t-mute mt-2 text-[12px] leading-4">
                Открыто {fmtNum(pool.size)}, простаивает {fmtNum(pool.idle)}, минимум {fmtNum(pool.min)}
              </p>
            </>
          ) : (
            <p className="t-mute text-[13px]">Пул: нет данных</p>
          )}
        </div>
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="steel" label="Telegram webhook" hint={DEF.webhook}>
        <StateLine
          tone={wh.ok ? "ok" : "err"}
          text={wh.ok ? "Доставляет" : "Проблема"}
          meta={
            wh.error
              ? wh.error
              : wh.url_set === false
                ? "URL вебхука не задан"
                : [wh.url_host, wh.latency_ms != null ? `ответ за ${fmtMs(wh.latency_ms)}` : null].filter(Boolean).join(", ") || undefined
          }
        />
        <div className="mt-4 flex flex-col gap-1.5">
          <Fact label="Очередь обновлений" value={wh.pending_update_count == null ? "—" : fmtNum(wh.pending_update_count)} />
          {wh.max_connections != null && <Fact label="Соединений максимум" value={fmtNum(wh.max_connections)} />}
        </div>
        <p className="t-mute mt-3 break-words text-[12px] leading-4">
          {wh.last_error_message
            ? `Последняя ошибка ${wh.last_error_at ? fmtRelative(wh.last_error_at) : wh.last_error_age_s != null ? `${fmtDuration(wh.last_error_age_s)} назад` : ""}: ${wh.last_error_message}`
            : "Ошибок доставки Telegram не сообщает"}
        </p>
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="fog" label="Панель Remnawave">
        {!h.remnawave.enabled ? (
          <StateLine tone="idle" text="Выключена" meta="Интеграция с панелью не настроена" />
        ) : h.remnawave.ok ? (
          <StateLine tone="ok" text="Отвечает" meta={`за ${fmtMs(h.remnawave.latency_ms)}`} />
        ) : (
          <StateLine tone="err" text="Не отвечает" meta={h.remnawave.error ?? undefined} />
        )}
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="mist" label="Redis">
        {!h.redis.configured ? (
          <StateLine tone="idle" text="Не настроен" meta="Бот работает без него" />
        ) : h.redis.ok ? (
          <StateLine tone="ok" text="Отвечает" meta={`за ${fmtMs(h.redis.latency_ms)}`} />
        ) : (
          <StateLine tone="err" text="Не отвечает" meta={h.redis.error ?? undefined} />
        )}
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="raised" label="Версия и аптайм">
        <div className={BIG}>{fmtDuration(h.uptime.uptime_s)}</div>
        <p className="t-mute text-[13px] leading-5">
          работает{h.uptime.started_at ? ` с ${fmtDate(h.uptime.started_at)}` : ""}
        </p>
        <div className="mt-3 flex flex-col gap-1.5">
          <Fact
            label="Коммит"
            value={
              h.version.git_sha ? (
                <span className="font-mono text-[12px]">
                  {h.version.git_sha.slice(0, 7)}
                  {h.version.git_branch ? ` (${h.version.git_branch})` : ""}
                </span>
              ) : (
                "—"
              )
            }
          />
          <Fact label="Окружение" value={h.version.environment ?? "—"} />
          <Fact label="Сборка дашборда" value={fmtRelative(h.version.dashboard_built_at)} />
        </div>
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-3" variant="steel" label="Алерты админу за 24 ч" hint={DEF.alerts_24h}>
        <div className={BIG}>{fmtNum(h.alerts.total)}</div>
        <p className="t-mute text-[13px] leading-5">
          {alertsPartial ? `с момента запуска ${fmtDuration(h.alerts.covered_s)} назад` : "сообщений отправлено"}
        </p>
        {Object.keys(h.alerts.by_category).length > 0 && (
          <div className="mt-3 flex flex-col gap-1.5">
            {Object.entries(h.alerts.by_category).map(([k, n]) => (
              <Fact key={k} label={alertCategory(k)} value={fmtNum(n)} />
            ))}
          </div>
        )}
      </Surface>

      <Surface
        className="sm:col-span-6 xl:col-span-12"
        label="Воркеры"
        hint={DEF.workers}
        aside={
          h.workers.length ? (
            <span className="t-mute text-[12px]">
              работают {fmtNum(workersOk)} из {fmtNum(h.workers.length)}
            </span>
          ) : undefined
        }
      >
        {flagsOff.length > 0 && (
          <ul className="mb-3 flex flex-col gap-1.5">
            {flagsOff.map((t) => (
              <li key={t} className="flex items-start gap-2.5 text-[13px] leading-5">
                <span className="mt-1.5 flex-none">
                  <StatusDot tone="warn" />
                </span>
                <span className="min-w-0">{t}</span>
              </li>
            ))}
          </ul>
        )}
        {workers.length === 0 ? (
          <EmptyState title="Воркеры не отчитывались" hint="Список появится после первого цикла любого фонового воркера." />
        ) : (
          <ul className="grid grid-cols-1 gap-2 xl:grid-cols-2">
            {workers.map((w) => (
              <li key={w.name}>
                <WorkerRow w={w} />
              </li>
            ))}
          </ul>
        )}
      </Surface>
    </Bento>
  );
}

// ── 2. Payments ──────────────────────────────────────────────────────

const SILENCE: Record<ProviderHealth["state"], { tone: Tone; text: string }> = {
  ok: { tone: "ok", text: "В ритме" },
  silent: { tone: "warn", text: "Молчит" },
  rare: { tone: "idle", text: "Мало оплат, не оцениваем" },
  disabled: { tone: "idle", text: "Выключен" },
};

function enabledText(v: boolean | null | undefined): string {
  if (v == null) return "—";
  return v ? "да" : "нет";
}

function ProvidersTable({ p }: { p: PaymentsHealth }) {
  const by24 = new Map<string, ProviderPerf>(p.providers_24h.map((x) => [x.provider, x]));
  const by7 = new Map<string, ProviderPerf>(p.providers_7d.map((x) => [x.provider, x]));
  const health = new Map<string, ProviderHealth>(p.providers.map((x) => [x.provider, x]));
  const keys: string[] = [];
  for (const k of [...p.providers.map((x) => x.provider), ...by24.keys(), ...by7.keys(), ...Object.keys(p.last_paid)]) {
    if (!keys.includes(k)) keys.push(k);
  }
  if (keys.length === 0) return <EmptyState title="За неделю не было счетов" />;
  return (
    <div className="-mx-2 overflow-x-auto px-2">
      <table className="dtable min-w-[720px]">
        <thead>
          <tr>
            <th scope="col">Провайдер</th>
            <th scope="col">Включён</th>
            <th scope="col" className="num">
              <span className="inline-flex items-center gap-1.5">
                Конверсия 24 ч <Hint text={DEF.provider_conversion} label="Как считается: конверсия" />
              </span>
            </th>
            <th scope="col" className="num">
              <span className="inline-flex items-center gap-1.5">
                Доходимость 7 дн <Hint text={DEF.provider_success} label="Как считается: доходимость" />
              </span>
            </th>
            <th scope="col">Последняя оплата</th>
            <th scope="col">
              <span className="inline-flex items-center gap-1.5">
                Ритм <Hint text={DEF.silence} label="Как считается: ритм оплат" />
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const d24 = by24.get(k);
            const d7 = by7.get(k);
            const hp = health.get(k);
            const lastPaid = hp?.last_paid_at ?? p.last_paid[k] ?? null;
            const sil = hp ? SILENCE[hp.state] : null;
            return (
              <tr key={k}>
                <td className="font-medium">{providerLabel(k)}</td>
                <td>{enabledText(hp?.enabled)}</td>
                <td className="num">
                  {d24 ? (
                    <>
                      <div className="font-medium">{fmtPct(d24.conversion)}</div>
                      <div className="t-mute text-[12px]">
                        {fmtNum(d24.paid)} из {fmtNum(d24.created)}
                      </div>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="num">
                  {d7 ? (
                    <>
                      <div className="font-medium">{fmtPct(d7.success_rate)}</div>
                      <div className="t-mute text-[12px]">брошено {fmtNum(d7.abandoned)}</div>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="whitespace-nowrap">{fmtRelative(lastPaid)}</td>
                <td>
                  {sil ? (
                    <>
                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <StatusDot tone={sil.tone} />
                        {sil.text}
                      </span>
                      {hp?.threshold_h != null && (
                        <div className="t-mute text-[12px]">порог {fmtNum(Math.round(hp.threshold_h * 10) / 10)} ч</div>
                      )}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ErrorsByDay({ ops }: { ops: OperationsReport }) {
  const daily = useMemo(() => {
    const acc = new Map<string, number>();
    for (const r of ops.errors_daily) acc.set(r.date, (acc.get(r.date) ?? 0) + r.count);
    return [...acc.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, count]) => ({ date, count }));
  }, [ops]);
  if (daily.length === 0) return <EmptyState title="Ошибок за две недели не было" />;
  return <BarsChart data={daily} x="date" y="count" label="Ошибки" format={fmtNum} xFormat={fmtDay} />;
}

function RecentErrors({ ops }: { ops: OperationsReport }) {
  if (ops.errors_recent.length === 0) {
    return <EmptyState title="Ошибок нет" hint="Сюда попадают отклонённые вебхуки, несовпадения суммы и сбои выдачи." />;
  }
  return (
    <ul className="flex flex-col gap-2">
      {ops.errors_recent.map((e) => (
        <li key={e.id}>
          <ListRow
            leading={<StatusDot tone="err" />}
            title={`${stageLabel(e.stage)}${e.payment_provider ? `, ${providerLabel(e.payment_provider)}` : ""}`}
            meta={[
              e.created_at ? fmtRelative(e.created_at) : null,
              e.username ? `@${e.username}` : e.telegram_id,
              e.error_message,
            ]
              .filter(Boolean)
              .join(". ")}
            value={e.amount_rubles != null ? fmtRub(e.amount_rubles) : undefined}
            to={e.telegram_id ? `/users?tg=${e.telegram_id}` : undefined}
          />
        </li>
      ))}
    </ul>
  );
}

function PaymentsSection({
  p,
  ops,
  opsError,
  onOpsRetry,
}: {
  p: PaymentsHealth;
  ops: OperationsReport | undefined;
  opsError: unknown;
  onOpsRetry: () => void;
}) {
  const ttpTotal = p.time_to_pay.find((t) => t.provider == null);
  const ttpByProvider = p.time_to_pay.filter((t) => t.provider != null);
  const stuckTotal = p.stuck.reduce((s, x) => s + x.count, 0);
  const err = p.errors_24h;

  const opsBody = (render: (o: OperationsReport) => ReactNode, skeleton: string) =>
    ops ? render(ops) : opsError ? <SectionError error={opsError} onRetry={onOpsRetry} /> : <Skeleton className={skeleton} />;

  return (
    <Bento>
      <Surface className="sm:col-span-6 xl:col-span-4" label="Вердикт">
        <StateLine tone={statusTone(p.verdict.status)} text={statusLabel(p.verdict.status)} />
        <div className="mt-4">
          <ReasonList reasons={p.verdict.reasons} empty="Ошибок нет, провайдеры в своём ритме" />
        </div>
      </Surface>

      <Surface className="sm:col-span-6 xl:col-span-8" variant="raised" label="Провайдеры">
        <ProvidersTable p={p} />
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-4" variant="steel" label="Время до оплаты" hint={DEF.time_to_pay}>
        {ttpTotal ? (
          <>
            <div className={BIG}>{fmtDuration(ttpTotal.p50_s)}</div>
            <p className="t-mute text-[13px] leading-5">
              медиана, 90% быстрее {fmtDuration(ttpTotal.p90_s)}. Оплат {fmtNum(ttpTotal.count)}
            </p>
          </>
        ) : (
          <p className="text-[14px]">За 7 дней оплат не было</p>
        )}
        {ttpByProvider.length > 0 && (
          <div className="mt-4 flex flex-col gap-1.5">
            {ttpByProvider.map((t) => (
              <Fact
                key={t.provider}
                label={`${providerLabel(t.provider)} (${fmtNum(t.count)})`}
                value={`${fmtDuration(t.p50_s)} / ${fmtDuration(t.p90_s)}`}
              />
            ))}
            <p className="t-mute mt-1 text-[12px] leading-4">медиана / 90-й перцентиль</p>
          </div>
        )}
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-4" variant="fog" label="Неоплаченные счета" hint={DEF.stuck}>
        <div className={BIG}>{fmtNum(stuckTotal)}</div>
        <p className="t-mute text-[13px] leading-5">висят от 30 минут до суток</p>
        {p.stuck.length > 0 && (
          <div className="mt-4 flex flex-col gap-1.5">
            {p.stuck.map((s) => (
              <Fact
                key={s.provider}
                label={providerLabel(s.provider)}
                value={
                  <>
                    {fmtNum(s.count)}
                    <span className="t-mute ml-1.5 font-normal">
                      {s.created_24h > 0 ? `${fmtPct((s.count / s.created_24h) * 100)} счетов` : "—"}
                    </span>
                  </>
                }
              />
            ))}
            <p className="t-mute mt-1 text-[12px] leading-4">доля — от счетов, открытых у провайдера за сутки</p>
          </div>
        )}
      </Surface>

      <Surface className="sm:col-span-6 xl:col-span-4" variant="raised" label="Ошибки за 24 ч" hint={DEF.errors_24h}>
        <div className={BIG}>{fmtNum(err.total)}</div>
        <p className="t-mute text-[13px] leading-5">из них в оплатах Telegram {fmtNum(err.telegram_money)}</p>
        {err.matrix.length > 0 && (
          <ul className="mt-4 flex flex-col gap-2">
            {err.matrix.map((m) => (
              <li key={`${m.stage}:${m.provider}`}>
                <ListRow
                  leading={<StatusDot tone="err" />}
                  title={stageLabel(m.stage)}
                  meta={`${providerLabel(m.provider)}${m.last_at ? `, последняя ${fmtRelative(m.last_at)}` : ""}`}
                  value={fmtNum(m.count)}
                />
              </li>
            ))}
          </ul>
        )}
      </Surface>

      <Surface
        className="sm:col-span-6 xl:col-span-7"
        label="Ошибки по дням"
        hint={DEF.errors_daily}
      >
        {opsBody((o) => <ErrorsByDay ops={o} />, "h-44 w-full")}
      </Surface>

      <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Последние ошибки" hint={DEF.errors_24h}>
        {opsBody((o) => <RecentErrors ops={o} />, "h-44 w-full")}
      </Surface>
    </Bento>
  );
}

// ── 3. Delivery ──────────────────────────────────────────────────────

function retrySql(ids: number[]): string {
  const list = ids.join(", ");
  return [
    `SELECT id, telegram_id, last_error FROM provisioning_jobs WHERE id IN (${list});`,
    "",
    `UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id IN (${list});`,
  ].join("\n");
}

function DeadJobs({ jobs }: { jobs: DeliveryHealth["dead_jobs"] }) {
  if (jobs.length === 0) {
    return <EmptyState title="Неудачных выдач нет" hint="Всё оплаченное выдано или ещё в работе." />;
  }
  const sql = retrySql(jobs.map((j) => j.id));
  const hasConflict = jobs.some((j) => j.last_error?.includes("conflict:"));
  return (
    <>
      <div className="-mx-2 overflow-x-auto px-2">
        <table className="dtable min-w-[760px]">
          <thead>
            <tr>
              <th scope="col" className="num">id</th>
              <th scope="col">Telegram ID</th>
              <th scope="col">Источник</th>
              <th scope="col">Тариф</th>
              <th scope="col" className="num">Попыток</th>
              <th scope="col">Ошибка</th>
              <th scope="col">Обновлено</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td className="num font-mono text-[12px]">{j.id}</td>
                <td>
                  <Link to={`/users?tg=${j.telegram_id}`} className="tap-target tabular underline-offset-2 hover:underline">
                    {j.telegram_id}
                  </Link>
                </td>
                <td>{j.source}</td>
                <td>{j.tariff_key}</td>
                <td className="num">{fmtNum(j.attempts)}</td>
                <td className="max-w-[260px]">
                  <span className="block truncate" title={j.last_error ?? undefined}>
                    {j.last_error ?? "—"}
                  </span>
                </td>
                <td className="whitespace-nowrap">{fmtRelative(j.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={cn(NESTED, "mt-4")}>
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-[14px] font-medium">Повторить вручную</p>
          <CopyButton value={sql} label="SQL" />
        </div>
        <p className="t-mute mb-2 text-[12px] leading-4">
          Кнопки повтора здесь нет. Запрос собран из заданий в таблице выше.
        </p>
        <pre className="t-body overflow-x-auto whitespace-pre rounded-row bg-tile-4 p-3 font-mono text-[12px] leading-5">
          {sql}
        </pre>
        <ul className="mt-3 flex flex-col gap-1.5 text-[13px] leading-5">
          <li className="flex items-start gap-2.5">
            <span className="mt-1.5 flex-none">
              <StatusDot tone={hasConflict ? "warn" : "idle"} />
            </span>
            <span className="min-w-0">
              Если в ошибке есть conflict:, обычный повтор снова упадёт. Сначала убедитесь, что ГБ пользователю не
              начислены, и добавьте в SET bypass_base_bytes=NULL, bypass_target_bytes=NULL.
            </span>
          </li>
          <li className="flex items-start gap-2.5">
            <span className="mt-1.5 flex-none">
              <StatusDot tone="idle" />
            </span>
            <span className="min-w-0">Выполняйте в консоли базы после SELECT с тем же WHERE.</span>
          </li>
        </ul>
      </div>
    </>
  );
}

const DELIVERY_ERRORS: { key: keyof DeliveryHealth["errors_24h"]; label: string; hint?: string }[] = [
  { key: "provisioning_dead", label: "Выдача: не удалась" },
  { key: "provisioning_retry", label: "Выдача: повтор после ошибки" },
  { key: "mismatch", label: "Панель не совпала с оплатой", hint: DEF.mismatch },
  { key: "renewal_sync", label: stageLabel("renewal_sync") },
  { key: "bypass_topup", label: stageLabel("bypass_topup") },
];

function DeliverySection({ d }: { d: DeliveryHealth }) {
  const q = d.queue;
  return (
    <Bento>
      <Surface className="sm:col-span-6 xl:col-span-4" label="Вердикт">
        <StateLine tone={statusTone(d.verdict.status)} text={statusLabel(d.verdict.status)} />
        <div className="mt-4">
          <ReasonList reasons={d.verdict.reasons} empty="Всё оплаченное выдано" />
        </div>
      </Surface>

      <Surface className="sm:col-span-6 xl:col-span-8" variant="raised" label="Очередь выдачи" hint={DEF.queue}>
        {!q.available ? (
          <EmptyState
            title="Очереди выдачи ещё нет"
            hint="Таблицы provisioning_jobs нет в базе, поэтому счётчиков очереди нет. Они появятся после миграции."
          />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <Stat label="новые" value={fmtNum(q.pending_new)} />
              <Stat label="повторяются" value={fmtNum(q.retrying)} />
              <Stat label="в работе" value={fmtNum(q.running)} />
              <Stat label="dead" value={fmtNum(q.dead)} sub={q.dead_24h != null ? `за 24 ч ${fmtNum(q.dead_24h)}` : undefined} />
              <Stat label="выполнено за 24 ч" value={fmtNum(q.done_24h)} />
              <Stat
                label="самое старое открытое"
                value={q.oldest_open_age_s == null ? "—" : fmtDuration(q.oldest_open_age_s)}
                sub={q.max_attempts_open ? `до ${fmtNum(q.max_attempts_open)} попыток` : undefined}
              />
            </div>
          </>
        )}
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-6" variant="steel" label="Активации" hint={DEF.activations}>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <div className={BIG}>{fmtNum(d.activations.pending)}</div>
            <p className="t-mute text-[13px]">ждут</p>
          </div>
          <div>
            <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(d.activations.failed)}</div>
            <p className="t-mute text-[13px]">с ошибкой</p>
          </div>
          <div>
            <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(d.activations.max_attempts)}</div>
            <p className="t-mute text-[13px]">больше всего попыток</p>
          </div>
        </div>
      </Surface>

      <Surface className="sm:col-span-3 xl:col-span-6" variant="fog" label="Ошибки выдачи за 24 ч" hint={DEF.errors_24h}>
        <div className="flex flex-col gap-1.5">
          {DELIVERY_ERRORS.map((e) => {
            const v = d.errors_24h[e.key];
            return <Fact key={e.key} label={e.label} hint={e.hint} value={v == null ? "—" : fmtNum(v)} />;
          })}
        </div>
      </Surface>

      <Surface
        className="sm:col-span-6 xl:col-span-12"
        label="Не удалось выдать"
        hint={DEF.dead_jobs}
        aside={d.dead_jobs.length ? <span className="t-mute text-[12px]">{fmtNum(d.dead_jobs.length)}</span> : undefined}
      >
        <DeadJobs jobs={d.dead_jobs} />
      </Surface>
    </Bento>
  );
}

// ── Screen ───────────────────────────────────────────────────────────

export function Health() {
  const health = useQuery({ queryKey: ["m-health"], queryFn: metricsApi.health, refetchInterval: 30_000 });
  const payments = useQuery({ queryKey: ["m-payments-health"], queryFn: metricsApi.paymentsHealth, refetchInterval: 60_000 });
  const delivery = useQuery({ queryKey: ["m-delivery"], queryFn: metricsApi.delivery, refetchInterval: 30_000 });
  const ops = useQuery({ queryKey: ["m-ops", 24], queryFn: () => metricsApi.operations(24), refetchInterval: 60_000 });

  const fetching = health.isFetching || payments.isFetching || delivery.isFetching || ops.isFetching;
  const refetchAll = () => {
    void health.refetch();
    void payments.refetch();
    void delivery.refetch();
    void ops.refetch();
  };

  const h = health.data;
  const p = payments.data;
  const d = delivery.data;

  return (
    <>
      <PageHeader
        title="Здоровье"
        sub="Работает ли бот сейчас: система, платежи, выдача доступа."
        actions={
          <>
            {h && <ShellStatus status={h.overall.status} />}
            {h && <span className="on-shell-mute text-[13px]">проверено {fmtRelative(h.checked_at)}</span>}
            <IconButton label="Обновить" onClick={refetchAll} disabled={fetching}>
              <RefreshCw className={cn("h-4 w-4", fetching && "animate-spin")} strokeWidth={2} />
            </IconButton>
          </>
        }
      />

      <SectionHeader title="Система" hint={DEF.system_status} />
      {h ? (
        <SystemSection h={h} />
      ) : health.isError ? (
        <SectionError error={health.error} onRetry={() => health.refetch()} />
      ) : (
        <LoadingTiles count={4} />
      )}

      <SectionHeader title="Платежи" aside={p ? <ShellStatus status={p.verdict.status} /> : undefined} />
      {p ? (
        <PaymentsSection p={p} ops={ops.data} opsError={ops.isError ? ops.error : null} onOpsRetry={() => ops.refetch()} />
      ) : payments.isError ? (
        <SectionError error={payments.error} onRetry={() => payments.refetch()} />
      ) : (
        <LoadingTiles count={4} />
      )}

      <SectionHeader title="Выдача доступа" aside={d ? <ShellStatus status={d.verdict.status} /> : undefined} />
      {d ? (
        <DeliverySection d={d} />
      ) : delivery.isError ? (
        <SectionError error={delivery.error} onRetry={() => delivery.refetch()} />
      ) : (
        <LoadingTiles count={4} />
      )}

      <SectionHeader title="Инструменты" />
      <Bento>
        <RemnawaveBackfillCard className="sm:col-span-6 xl:col-span-12" />
      </Bento>
    </>
  );
}
