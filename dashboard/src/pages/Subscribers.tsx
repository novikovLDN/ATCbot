/**
 * Subscribers — who has access and why, who is about to lose it, how
 * many renew, how trials convert, and the acquisition funnel.
 * Source: /metrics/subscribers (database/metrics.py); every "?" reads
 * lib/metricDefs.
 */
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Loader2 } from "lucide-react";
import { KIND_LABEL, metricsApi, type Pipeline, type SubKind, type SubscribersReport } from "@/lib/metricsApi";
import { DEF } from "@/lib/metricDefs";
import { WINDOWS } from "@/lib/periods";
import { downloadCsv } from "@/lib/api";
import { toast } from "@/store/toast";
import { fmtDay, fmtKop, fmtNum, fmtPct } from "@/lib/format";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Hint } from "@/components/ui/Hint";
import { DeltaPill, ListRow, PillProgress, Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { ShareList } from "@/components/ui/charts";
import { EmptyState, ErrorState, LoadingTiles } from "@/components/ui/states";

const KIND_ORDER: SubKind[] = ["paid", "gift", "granted", "trial", "bypass_only"];
const PIPELINE_KINDS: (keyof Pipeline["by_kind"])[] = ["paid", "trial", "granted", "gift"];
const KIND_TONE: Record<SubKind, string> = {
  paid: "rgb(var(--c-accent))",
  gift: "rgb(var(--c-ink) / 0.7)",
  granted: "rgb(var(--c-ink) / 0.5)",
  trial: "rgb(var(--c-ink) / 0.32)",
  bypass_only: "rgb(var(--c-ink) / 0.16)",
};
const RECURRING_TONE: Record<string, Tone> = { Active: "ok", PastDue: "warn", Canceled: "idle", PendingAgreement: "info" };
const SOURCE_LABEL: Record<string, string> = {
  admin: "админ",
  admin_grant: "админ",
  referral: "реферал",
  promo_link: "промо-ссылка",
  unknown: "без источника",
};

function signed(n: number): string {
  if (n > 0) return `+${fmtNum(n)}`;
  if (n < 0) return `−${fmtNum(Math.abs(n))}`;
  return "0";
}

/** A label with its "?" outside any truncating box, so the tap target is not clipped. */
function Titled({ children, hint }: { children: ReactNode; hint: string }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="truncate">{children}</span>
      <Hint text={hint} label={typeof children === "string" ? `Как считается: ${children}` : undefined} />
    </span>
  );
}

function ExportButton() {
  const [pending, setPending] = useState(false);
  async function run() {
    if (pending) return;
    setPending(true);
    try {
      await downloadCsv("/export/subscriptions.csv", `subscriptions_${new Date().toISOString().slice(0, 10)}.csv`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось выгрузить CSV");
    } finally {
      setPending(false);
    }
  }
  return (
    <button type="button" className="btn-secondary min-h-[44px]" onClick={run} disabled={pending} aria-busy={pending}>
      {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="h-4 w-4" aria-hidden="true" />}
      {pending ? "Экспорт…" : "Экспорт CSV"}
    </button>
  );
}

function DayBars({ days }: { days: Pipeline["by_day"] }) {
  const max = Math.max(1, ...days.map((d) => d.total));
  return (
    <div className="flex h-24 gap-1.5" role="img" aria-label="Истекает по дням, из них с автопродлением">
      {days.map((d) => {
        const wd = new Date(`${d.date}T12:00:00Z`).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "Europe/Moscow" });
        return (
          <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1" title={`${fmtDay(d.date)}: ${d.total}, с автопродлением ${d.auto_renew}`}>
            {/* Pixel heights: WebKit does not resolve % heights inside flex-grown items. */}
            <div className="flex w-full flex-col justify-end overflow-hidden rounded-[6px] bg-[rgb(var(--c-ink)/0.06)]" style={{ height: 76 }}>
              <div className="w-full bg-[rgb(var(--c-ink)/0.28)]" style={{ height: Math.round(((d.total - d.auto_renew) / max) * 76) }} />
              <div className="w-full bg-accent" style={{ height: Math.round((d.auto_renew / max) * 76) }} />
            </div>
            <span className="t-mute text-[11px] leading-3">{wd}</span>
          </div>
        );
      })}
    </div>
  );
}

function TrialRate({
  label,
  hint,
  horizon,
  matured,
  paid,
  rate,
}: {
  label: string;
  hint: string;
  horizon: number;
  matured: number;
  paid: number;
  rate: number | null;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-3 text-[13px]">
        <Titled hint={hint}>{label}</Titled>
        {matured > 0 && <span className="tabular flex-none font-semibold">{fmtPct(rate)}</span>}
      </div>
      {matured > 0 ? (
        <>
          <PillProgress value={rate} knob={false} />
          <p className="t-mute mt-1.5 text-[12px] leading-4">
            Из {fmtNum(matured)} пробников, которым уже {horizon} дней, оплатили {fmtNum(paid)}.
          </p>
        </>
      ) : (
        <p className="t-mute text-[13px] leading-5">Пока нет пробников старше {horizon} дней.</p>
      )}
    </div>
  );
}

function PipelineBlock({ pipe }: { pipe: Pipeline }) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <div className="tabular text-[30px] font-semibold leading-9">{fmtNum(pipe.expiring)}</div>
            <p className="t-mute text-[13px]">подписок истекает</p>
          </div>
          <div>
            <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(pipe.auto_renew)}</div>
            <p className="t-mute text-[13px]">продлятся автоматически</p>
          </div>
          <div>
            <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(pipe.manual)}</div>
            <p className="t-mute text-[13px]">только сами</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {PIPELINE_KINDS.map((k) => (
            <span key={k} className="capsule-label tabular">
              {KIND_LABEL[k]} {fmtNum(pipe.by_kind[k] ?? 0)}
            </span>
          ))}
        </div>
        {pipe.by_day.length > 0 && (
          <div className="mt-4">
            <DayBars days={pipe.by_day} />
            <p className="t-mute mt-2 flex flex-wrap items-center gap-3 text-[12px]">
              <span className="inline-flex items-center gap-1.5">
                <span className="dot" style={{ background: "rgb(var(--c-accent))" }} aria-hidden="true" /> с автопродлением
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="dot" style={{ background: "rgb(var(--c-ink) / 0.28)" }} aria-hidden="true" /> без
              </span>
            </p>
          </div>
        )}
      </div>
      <ul className="flex flex-col gap-2">
        <li>
          <ListRow
            title={<Titled hint={DEF.pipeline_expected}>Спишется с балансов</Titled>}
            meta="оценка по прейскуранту, до персональных скидок"
            value={fmtKop(pipe.expected_list_kopecks)}
          />
        </li>
        <li>
          <ListRow
            leading={<StatusDot tone="ok" />}
            title={<Titled hint={DEF.pipeline_covered}>Баланса хватит</Titled>}
            meta={`на ${fmtKop(pipe.covered_kopecks)}`}
            value={fmtNum(pipe.covered)}
          />
        </li>
        <li>
          <ListRow
            leading={<StatusDot tone={pipe.not_covered ? "warn" : "idle"} />}
            title={<Titled hint={DEF.pipeline_short}>Не хватит без пополнения</Titled>}
            meta={`не хватает ${fmtKop(pipe.shortfall_kopecks)}`}
            value={fmtNum(pipe.not_covered)}
            to="/users"
          />
        </li>
        {pipe.unpriced > 0 && (
          <li>
            <ListRow leading={<StatusDot tone="idle" />} title="Цену не определить" meta="неизвестный период комбо" value={fmtNum(pipe.unpriced)} />
          </li>
        )}
        <li>
          <ListRow leading={<StatusDot tone="warn" />} title="Напомнить тем, кто без автопродления" meta="рассылки" to="/broadcasts" />
        </li>
      </ul>
    </div>
  );
}

function MotionBlock({ m }: { m: SubscribersReport["motion"] }) {
  return (
    <>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <div className="rounded-row bg-tile-3 p-3">
          <p className="text-[14px] font-medium">Новые</p>
          <div className="tabular mt-1 text-[22px] font-semibold leading-7">{fmtNum(m.new.count)}</div>
          <p className="t-mute text-[12px] leading-4">
            на {fmtKop(m.new.kopecks)}, {fmtNum(m.new.users)} чел.
          </p>
        </div>
        <div className="rounded-row bg-tile-3 p-3">
          <p className="text-[14px] font-medium">Продления</p>
          <div className="tabular mt-1 text-[22px] font-semibold leading-7">{fmtNum(m.renewal.count)}</div>
          <p className="t-mute text-[12px] leading-4">на {fmtKop(m.renewal.kopecks)}</p>
        </div>
        <div className="rounded-row bg-tile-3 p-3">
          <p className="text-[14px] font-medium">Автопродления с баланса</p>
          <div className="tabular mt-1 text-[22px] font-semibold leading-7">{fmtNum(m.auto_renew_balance.count)}</div>
          <p className="t-mute text-[12px] leading-4">
            на {fmtKop(m.auto_renew_balance.kopecks)}. С баланса — деньги уже посчитаны при пополнении, в выручку не входят.
          </p>
        </div>
      </div>
      <p className="t-mute mt-3 text-[13px] leading-5">
        Новые — {fmtPct(m.new_share)} прямых оплат подписок (новые ÷ новые + продления).
      </p>
    </>
  );
}

export function Subscribers() {
  const [days, setDays] = useState(30);
  const q = useQuery({
    queryKey: ["m-subs", days],
    queryFn: () => metricsApi.subscribers(days),
    placeholderData: (prev) => prev,
    refetchInterval: 120_000,
  });
  const d = q.data;
  const header = (
    <PageHeader
      title="Подписчики"
      sub="Активной считается подписка со статусом active и сроком в будущем — тот же признак, по которому бот отключает доступ."
      actions={
        <>
          <Segmented label="Период" value={days} options={WINDOWS} onChange={setDays} />
          <ExportButton />
        </>
      }
    />
  );
  if (!d) {
    return (
      <>
        {header}
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <LoadingTiles count={6} />}
      </>
    );
  }

  const a = d.active;
  const total = KIND_ORDER.reduce((s, k) => s + (a.by_kind[k] ?? 0), 0) || 1;
  const r = d.renewals;
  const rp = d.renewals_prev;
  const renewalDelta = r.renewal_rate != null && rp.renewal_rate != null ? r.renewal_rate - rp.renewal_rate : null;
  const grantedMeta = Object.entries(a.granted_sources ?? {})
    .sort((x, y) => y[1] - x[1])
    .map(([src, n]) => `${SOURCE_LABEL[src] ?? src} ${fmtNum(n)}`)
    .join(", ");
  const g = d.growth;
  const t = d.trial;

  return (
    <div className={q.isFetching ? "opacity-80 transition-opacity" : "transition-opacity"}>
      {header}
      {q.isError && <ErrorState className="mb-3" error={q.error} onRetry={() => q.refetch()} />}
      <Bento>
        <KpiTile
          className="sm:col-span-6 xl:col-span-4"
          size="hero"
          label="С доступом"
          value={fmtNum(a.with_access)}
          sub={`Платных ${fmtNum(a.paid)}. Ещё ${fmtNum(a.by_kind.bypass_only)} только с обходом.`}
          to="/users"
          hint={DEF.with_access}
        />
        <Surface className="sm:col-span-6 xl:col-span-8" variant="raised" label="Кто и почему с доступом" hint={DEF.granted}>
          <div className="flex h-4 overflow-hidden rounded-full" role="img" aria-label="Доли видов подписок">
            {KIND_ORDER.map((k) => (
              <div key={k} style={{ width: `${((a.by_kind[k] ?? 0) / total) * 100}%`, background: KIND_TONE[k] }} />
            ))}
          </div>
          <ul className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
            {KIND_ORDER.map((k) => {
              const pct = `${fmtPct(((a.by_kind[k] ?? 0) / total) * 100)} от всех активных`;
              return (
                <li key={k}>
                  <ListRow
                    leading={<span className="dot" style={{ background: KIND_TONE[k] }} aria-hidden="true" />}
                    title={KIND_LABEL[k]}
                    meta={
                      k === "granted" && grantedMeta ? (
                        <span className="whitespace-normal">
                          {grantedMeta}. {pct}
                        </span>
                      ) : (
                        pct
                      )
                    }
                    value={fmtNum(a.by_kind[k] ?? 0)}
                  />
                </li>
              );
            })}
          </ul>
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-12" label="Истекают в ближайшие 7 дней" hint={DEF.pipeline}>
          {d.pipeline ? <PipelineBlock pipe={d.pipeline} /> : <EmptyState title="Не удалось посчитать" />}
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-4" variant="steel" label="Продления" hint={DEF.renewal_rate}>
          <div className="tabular text-[30px] font-semibold leading-9">{fmtPct(r.renewal_rate)}</div>
          <div className="mt-2">
            <DeltaPill pct={renewalDelta} period="п.п. к прошлому периоду" />
          </div>
          <div className="mt-4">
            <PillProgress value={r.renewal_rate} />
          </div>
          <p className="t-mute mt-4 text-[13px] leading-5">
            Закончилось оплаченных периодов: {fmtNum(r.ending)}. Продлили {fmtNum(r.renewed)}, ушли {fmtNum(r.churned)},
            ещё решают {fmtNum(r.open)} (прошло меньше {r.grace_days} дн.).
          </p>
          <p className="mt-2 inline-flex items-center gap-1.5 text-[13px]">
            Отток {fmtPct(r.churn_rate)} <Hint text={DEF.churn} label="Как считается: отток" />
          </p>
        </Surface>

        <KpiTile
          className="sm:col-span-3 xl:col-span-4"
          variant="raised"
          label="Чистый прирост"
          value={signed(g.net)}
          sub={`Новых платящих ${fmtNum(g.new_paying)}, ушли ${fmtNum(g.churned)}. Ещё решают: ${fmtNum(g.undecided)}.`}
          hint={DEF.growth}
        />

        <Surface className="sm:col-span-3 xl:col-span-4" variant="fog" label="Автопродление" hint={DEF.auto_renew_share}>
          <div className="tabular text-[30px] font-semibold leading-9">{fmtPct(a.auto_renew_share)}</div>
          <p className="t-mute mt-1 text-[13px]">платных подписок продлятся с баланса</p>
          <p className="mt-4 text-[13px] leading-5">
            Включено у {fmtNum(a.auto_renew_paid)} из {fmtNum(a.by_kind.paid)} платных.
          </p>
          {Object.keys(d.auto_renew.platega_recurring).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(d.auto_renew.platega_recurring).map(([st, n]) => (
                <span key={st} className="capsule-label">
                  <StatusDot tone={RECURRING_TONE[st] ?? "idle"} /> Platega {st}: {fmtNum(n)}
                </span>
              ))}
            </div>
          )}
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-12" variant="raised" label={`Продажи подписок за ${days} дн.`} hint={DEF.motion}>
          <MotionBlock m={d.motion} />
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-5" label="Пробный период → оплата">
          <p className="t-mute mb-4 text-[13px] leading-5">
            {fmtNum(t.trials)} человек взяли пробный период за {days} дн. Сколько из них потом оплатили подписку:
          </p>
          <div className="flex flex-col gap-4">
            <TrialRate label="За 7 дней" hint={DEF.trial_7d} horizon={7} matured={t.matured_7d} paid={t.paid_7d_matured} rate={t.rate_7d} />
            <TrialRate label="За 30 дней" hint={DEF.trial_30d} horizon={30} matured={t.matured_30d} paid={t.paid_30d_matured} rate={t.rate_30d} />
            <div>
              <div className="mb-2 flex items-baseline justify-between gap-3 text-[13px]">
                <Titled hint={DEF.trial_any}>К сегодняшнему дню</Titled>
                <span className="tabular flex-none font-semibold">
                  {fmtPct(t.rate_any)} · {fmtNum(t.paid_any)}
                </span>
              </div>
              <PillProgress value={t.rate_any} knob={false} />
              <p className="t-mute mt-1.5 text-[12px] leading-4">Незрелая доля: свежие пробники ещё могут оплатить.</p>
            </div>
          </div>
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-7" variant="raised" label="Воронка новых пользователей" hint={DEF.funnel}>
          <p className="t-mute mb-4 text-[13px] leading-5">Пришли в бот за {days} дн. и что сделали потом.</p>
          <ol className="flex flex-col gap-2">
            {d.funnel.steps.map((s) => (
              <li key={s.key}>
                <div className="list-row flex-wrap">
                  <span className="min-w-0 flex-1">
                    <span className="block text-[14px] font-medium">{s.label}</span>
                    {s.recorded ? (
                      <span className="t-mute block text-[12px]">
                        {s.of_start != null ? `${fmtPct(s.of_start)} от пришедших` : ""}
                        {s.of_prev != null && s.key !== "started" ? `, ${fmtPct(s.of_prev)} от прошлого шага` : ""}
                      </span>
                    ) : (
                      <span className="t-mute block text-[12px]">{s.note}</span>
                    )}
                  </span>
                  <span className="tabular text-[14px] font-semibold">{s.recorded ? fmtNum(s.users) : "нет данных"}</span>
                  {s.recorded && s.of_start != null && (
                    <div className="pill-track h-1.5 w-full basis-full">
                      <div className="pill-fill" style={{ width: `${Math.max(s.of_start, 1)}%` }} />
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-12" label="Тарифы активных подписок" hint={DEF.tariffs_active}>
          {Object.keys(a.by_tariff).length === 0 ? (
            <EmptyState title="Нет активных подписок" />
          ) : (
            <ShareList
              rows={Object.entries(a.by_tariff)
                .map(([k, v]) => ({ key: k, label: k === "basic" ? "Basic" : k === "plus" ? "Plus" : k, value: v, highlight: k === "plus" }))
                .sort((x, y) => y.value - x.value)}
              format={fmtNum}
            />
          )}
        </Surface>
      </Bento>
    </div>
  );
}
