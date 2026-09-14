/**
 * Overview — the owner's home screen, read top to bottom:
 *   1. Is anything broken? System, payments, delivery, panel — one word each.
 *   2. The period in numbers: KPIs against the same span before
 *      (today until now vs yesterday until the same time).
 *   3. What needs attention, and where the money came from.
 *   4. Subscribers: who has access, renewals, the 7-day renewal pipeline.
 *   5. Payments and delivery health.
 * Every figure comes from /metrics/overview (docs/dashboard/metrics.md);
 * every "?" reads lib/metricDefs.
 */
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { KIND_LABEL, PROVIDER_LABEL, metricsApi, type Alert, type Kpi, type OverviewReport, type SectionStatus, type SubKind } from "@/lib/metricsApi";
import { DEF } from "@/lib/metricDefs";
import { OVERVIEW_PERIODS, WINDOWS as SHARED_WINDOWS, comparedTo } from "@/lib/periods";
import { fmtCompactKop, fmtKop, fmtNum, fmtPct, fmtRelative } from "@/lib/format";
import { Bento, PageHeader, SectionHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Hint } from "@/components/ui/Hint";
import { DeltaPill, ListRow, PillProgress, Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { ReasonList, statusLabel, statusTone } from "@/components/ui/StatusBadge";
import { EmptyState, ErrorState, LoadingTiles } from "@/components/ui/states";

/** Kept for screens that still import it from here. */
export const WINDOWS = SHARED_WINDOWS;

const ALERT_TONE: Record<Alert["level"], Tone> = { critical: "err", warning: "warn", info: "info" };
const ALERT_LABEL: Record<Alert["level"], string> = { critical: "Критично", warning: "Внимание", info: "К сведению" };
const KIND_ORDER: SubKind[] = ["paid", "trial", "granted", "gift", "bypass_only"];
const SOURCE_LABEL: Record<string, string> = {
  admin: "админ",
  admin_grant: "админ",
  referral: "реферал",
  promo_link: "промо-ссылка",
  promo: "промокод",
  unknown: "без источника",
};
const LINES: { key: "shop" | "proxy" | "game"; label: string; def: string }[] = [
  { key: "shop", label: "Магазин", def: DEF.shop },
  { key: "proxy", label: "Прокси", def: DEF.proxy },
  { key: "game", label: "Игра", def: DEF.game },
];

function kpiValue(k: Kpi): string {
  return k.unit === "kopecks" ? fmtKop(k.value) : fmtNum(k.value);
}
function kpiPrev(k: Kpi): string {
  return k.unit === "kopecks" ? fmtKop(k.prev) : fmtNum(k.prev);
}

function panelStatus(p: OverviewReport["panel"]): SectionStatus {
  if (!p.checked) return "unknown";
  if (!p.available) return "critical";
  if (p.nodes_total != null && p.nodes_online != null && p.nodes_online < p.nodes_total) return "critical";
  return "ok";
}

function StatusTile({
  label,
  status,
  text,
  to,
  hint,
}: {
  label: string;
  status: SectionStatus | OverviewReport["health"]["status"];
  text: ReactNode;
  to: string;
  hint?: string;
}) {
  return (
    <Surface className="sm:col-span-3 xl:col-span-3" variant="raised" label={label} to={to} toLabel={label} hint={hint}>
      <div className="flex items-center gap-2 text-[18px] font-semibold leading-6">
        <StatusDot tone={statusTone(status)} />
        {statusLabel(status)}
      </div>
      <p className="t-mute mt-1.5 line-clamp-3 text-[13px] leading-5">{text}</p>
    </Surface>
  );
}

function Titled({ children, hint }: { children: ReactNode; hint: string }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="truncate">{children}</span>
      <Hint text={hint} />
    </span>
  );
}

const BAR_H = 76;

function DayBars({ days }: { days: { date: string; total: number; auto_renew: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.total));
  return (
    <div className="flex h-24 gap-1.5" role="img" aria-label="Истекает по дням, из них с автопродлением">
      {days.map((d) => {
        const wd = new Date(`${d.date}T12:00:00Z`).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "Europe/Moscow" });
        return (
          <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1" title={`${d.date}: ${d.total}, автопродление ${d.auto_renew}`}>
            {/* Pixel heights: WebKit does not resolve % heights inside flex-grown items. */}
            <div className="flex w-full flex-col justify-end overflow-hidden rounded-[6px] bg-[rgb(var(--c-ink)/0.06)]" style={{ height: BAR_H }}>
              <div className="w-full bg-[rgb(var(--c-ink)/0.28)]" style={{ height: Math.round(((d.total - d.auto_renew) / max) * BAR_H) }} />
              <div className="w-full bg-accent" style={{ height: Math.round((d.auto_renew / max) * BAR_H) }} />
            </div>
            <span className="t-mute text-[11px] leading-3">{wd}</span>
          </div>
        );
      })}
    </div>
  );
}

export function Overview() {
  const [days, setDays] = useState(7);
  const q = useQuery({
    queryKey: ["m-overview", days],
    queryFn: () => metricsApi.overview(days),
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const d = q.data;
  const header = (
    <PageHeader
      title="Обзор"
      sub={
        <span className="inline-flex flex-wrap items-center gap-1.5">
          Сравнение — с таким же отрезком раньше. Сутки по Москве.
          <Hint text={DEF.period} className="hint-on-shell" />
        </span>
      }
      actions={<Segmented label="Период" value={days} options={OVERVIEW_PERIODS} onChange={setDays} />}
    />
  );

  if (!d) {
    return (
      <>
        {header}
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <LoadingTiles count={8} />}
      </>
    );
  }

  const k = Object.fromEntries(d.kpis.map((x) => [x.key, x])) as Record<string, Kpi>;
  const period = comparedTo(days);
  const s = d.subscribers;
  const pipe = s.pipeline;
  const pay = d.payments;
  const del = d.delivery;
  const kindTotal = KIND_ORDER.reduce((sum, key) => sum + (s.by_kind[key] ?? 0), 0) || 1;
  const grantedMeta = Object.entries(s.granted_sources)
    .map(([src, n]) => `${SOURCE_LABEL[src] ?? src} ${fmtNum(n)}`)
    .join(", ");
  const small = (key: string, hint: string, variant: "raised" | "steel" | "fog" | "mist" | "ink" = "ink") =>
    k[key] && (
      <KpiTile
        key={key}
        className="sm:col-span-3 xl:col-span-3"
        size="md"
        variant={variant}
        label={k[key].label}
        value={kpiValue(k[key])}
        sub={`Было ${kpiPrev(k[key])}`}
        delta={k[key].delta_pct}
        hint={hint}
      />
    );

  return (
    <div className={q.isFetching ? "opacity-80 transition-opacity" : "transition-opacity"}>
      {header}
      {q.isError && <ErrorState className="mb-3" error={q.error} onRetry={() => q.refetch()} />}

      {/* 1. Status strip */}
      <Bento className="mb-[var(--gap)]">
        <StatusTile
          label="Система"
          status={d.health.status}
          to="/health"
          hint={DEF.system_status}
          text={
            d.health.reasons[0]?.text ??
            `База, Telegram, панель и воркеры в норме${d.health.checked_at ? `, проверено ${fmtRelative(d.health.checked_at)}` : ""}.`
          }
        />
        <StatusTile
          label="Платежи"
          status={pay.status}
          to="/health"
          hint={DEF.provider_conversion}
          text={
            pay.reasons[0]?.text ??
            `За сутки оплачено ${fmtPct(pay.conversion_24h)} счетов (${fmtNum(pay.paid_24h)} из ${fmtNum(pay.invoices_24h)}), ошибок нет.`
          }
        />
        <StatusTile
          label="Выдача доступа"
          status={del.status}
          to="/health"
          hint={DEF.queue}
          text={del.reasons[0]?.text ?? `В очереди ${fmtNum(del.queue_open)}, неудачных нет, активации без задержек.`}
        />
        <StatusTile
          label="Панель"
          status={panelStatus(d.panel)}
          to="/panel"
          hint={DEF.panel_online}
          text={
            !d.panel.checked
              ? "Панель не ответила за 4 секунды. Откройте экран панели, чтобы проверить ещё раз."
              : !d.panel.available
                ? "Панель Remnawave не отвечает."
                : `Онлайн ${fmtNum(d.panel.online_now)}. Ноды в сети: ${fmtNum(d.panel.nodes_online)} из ${fmtNum(d.panel.nodes_total)}.`
          }
        />
      </Bento>

      {/* 2. The period in numbers */}
      <Bento>
        {k.revenue && (
          <KpiTile
            className="sm:col-span-6 xl:col-span-6 xl:row-span-2"
            size="hero"
            label={k.revenue.label}
            value={fmtKop(k.revenue.value)}
            sub={
              <>
                Было {fmtKop(k.revenue.prev)}. {k.payments ? `${fmtNum(k.payments.value)} оплат, было ${fmtNum(k.payments.prev)}.` : ""}
              </>
            }
            delta={k.revenue.delta_pct}
            deltaPeriod={period}
            trend={d.series.map((p) => p.kopecks)}
            to="/money"
            hint={DEF.revenue}
          />
        )}
        {small("payers", DEF.payers, "raised")}
        {small("new_payers", DEF.new_payers, "steel")}
        {small("new_users", DEF.new_users, "raised")}
        {small("arppu", DEF.arppu, "fog")}

        {/* 3. Attention + where the money came from */}
        <Surface
          className="sm:col-span-6 xl:col-span-7"
          label="Требует внимания"
          aside={<span className="t-mute text-[12px]">{d.alerts.length ? fmtNum(d.alerts.length) : ""}</span>}
        >
          {d.alerts.length === 0 ? (
            <EmptyState
              title="Всё спокойно"
              hint="Нет сбоев системы и панели, ошибок платежей, молчащих провайдеров и зависших выдач."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {d.alerts.slice(0, 7).map((a) => (
                <li key={a.key}>
                  <ListRow
                    to={a.link}
                    leading={<StatusDot tone={ALERT_TONE[a.level]} label={ALERT_LABEL[a.level]} />}
                    title={a.title}
                    meta={a.detail}
                  />
                </li>
              ))}
            </ul>
          )}
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Деньги по линиям" hint={DEF.gross} to="/money" toLabel="Деньги">
          <ul className="flex flex-col gap-2">
            {k.revenue && (
              <li>
                <ListRow
                  title={<Titled hint={DEF.revenue}>Выручка VPN</Titled>}
                  meta={`было ${fmtCompactKop(k.revenue.prev)}`}
                  value={fmtCompactKop(k.revenue.value)}
                  trailing={<DeltaPill pct={k.revenue.delta_pct} />}
                />
              </li>
            )}
            {LINES.map((l) => (
              <li key={l.key}>
                <ListRow
                  title={<Titled hint={l.def}>{l.label}</Titled>}
                  meta={`отдельная строка, было ${fmtCompactKop(d.lines[l.key].prev)}`}
                  value={fmtCompactKop(d.lines[l.key].value)}
                  trailing={<DeltaPill pct={d.lines[l.key].delta_pct} />}
                />
              </li>
            ))}
            <li>
              <ListRow
                className="bg-tile-4"
                title={<Titled hint={DEF.gross}>Оборот</Titled>}
                meta="всё, что пришло извне"
                value={fmtCompactKop(d.lines.gross.value)}
                trailing={<DeltaPill pct={d.lines.gross.delta_pct} />}
              />
            </li>
          </ul>
        </Surface>
      </Bento>

      {/* 4. Subscribers */}
      <SectionHeader
        title="Подписчики"
        sub="Снимок на сейчас; продления — по периодам, закончившимся в выбранном окне."
        aside={
          <Link to="/subscribers" className="on-shell-mute tap-target text-[13px] underline-offset-2 hover:underline">
            Подробнее
          </Link>
        }
      />
      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="accent"
          label="С доступом"
          value={fmtNum(s.with_access)}
          sub={`Платных ${fmtNum(s.paid)}. Автопродление у ${fmtPct(s.auto_renew_share)} платных.`}
          to="/subscribers"
          hint={DEF.with_access}
        />
        <Surface className="sm:col-span-3 xl:col-span-5" label="Кто с доступом" hint={DEF.granted}>
          <ul className="flex flex-col gap-2.5">
            {KIND_ORDER.map((key) => {
              const n = s.by_kind[key] ?? 0;
              return (
                <li key={key}>
                  <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
                    <span className="min-w-0 truncate">
                      {KIND_LABEL[key]}
                      {key === "granted" && grantedMeta && <span className="t-mute ml-2 text-[12px]">{grantedMeta}</span>}
                    </span>
                    <span className="tabular flex-none font-medium">{fmtNum(n)}</span>
                  </div>
                  <div className="pill-track h-1.5">
                    <div
                      className="pill-fill"
                      style={{ width: `${Math.max((n / kindTotal) * 100, n ? 1.5 : 0)}%`, background: key === "paid" ? "rgb(var(--c-accent))" : undefined }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-4" variant="steel" label="Продления" hint={DEF.renewal_rate}>
          <div className="tabular text-[30px] font-semibold leading-9">{fmtPct(s.renewal_rate)}</div>
          <p className="t-mute mb-4 mt-1 text-[13px] leading-5">
            продлили закончившиеся оплаченные периоды. Ушли {fmtPct(s.churn_rate)}.
          </p>
          <PillProgress value={s.renewal_rate} />
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-12" label="Истекают в ближайшие 7 дней" hint={DEF.pipeline} to="/subscribers" toLabel="Подписчики">
          {!pipe ? (
            <p className="text-[14px]">Не удалось посчитать. Откройте «Подписчики», чтобы повторить.</p>
          ) : (
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
                <div className="mt-4">
                  <DayBars days={pipe.by_day} />
                  <p className="t-mute mt-2 flex items-center gap-3 text-[12px]">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="dot" style={{ background: "rgb(var(--c-accent))" }} aria-hidden="true" /> с автопродлением
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="dot" style={{ background: "rgb(var(--c-ink) / 0.28)" }} aria-hidden="true" /> без
                    </span>
                  </p>
                </div>
              </div>
              <ul className="flex flex-col gap-2">
                <li>
                  <ListRow
                    title={<Titled hint={DEF.pipeline_expected}>Спишется с балансов</Titled>}
                    meta="по прейскуранту, до персональных скидок"
                    value={fmtKop(pipe.expected_list_kopecks)}
                  />
                </li>
                <li>
                  <ListRow
                    leading={<StatusDot tone="ok" />}
                    title={<Titled hint={DEF.pipeline_covered}>Баланса хватит</Titled>}
                    meta={fmtKop(pipe.covered_kopecks)}
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
              </ul>
            </div>
          )}
        </Surface>
      </Bento>

      {/* 5. Payments and delivery */}
      <SectionHeader
        title="Платежи и выдача"
        sub="Последние 24 часа."
        aside={
          <Link to="/health" className="on-shell-mute tap-target text-[13px] underline-offset-2 hover:underline">
            Здоровье
          </Link>
        }
      />
      <Bento>
        <Surface className="sm:col-span-6 xl:col-span-6" label="Платежи" hint={DEF.provider_conversion} to="/health" toLabel="Здоровье">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="tabular text-[30px] font-semibold leading-9">{fmtPct(pay.conversion_24h)}</div>
              <p className="t-mute text-[13px]">счетов оплачено</p>
            </div>
            <div>
              <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(pay.invoices_24h)}</div>
              <p className="t-mute text-[13px]">счетов открыто</p>
            </div>
            <div>
              <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(pay.errors_24h)}</div>
              <p className="t-mute inline-flex items-center gap-1.5 text-[13px]">
                ошибок <Hint text={DEF.errors_24h} />
              </p>
            </div>
          </div>
          {pay.silent.length > 0 && (
            <p className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
              <StatusDot tone="warn" /> Молчат: {pay.silent.map((p) => PROVIDER_LABEL[p] ?? p).join(", ")}
              <Hint text={DEF.silence} />
            </p>
          )}
          <div className="mt-4">
            <ReasonList reasons={pay.reasons} empty="Ошибок нет, все провайдеры в своём ритме." limit={3} />
          </div>
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-6" variant="raised" label="Выдача доступа" hint={DEF.queue} to="/health" toLabel="Здоровье">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="tabular text-[30px] font-semibold leading-9">{del.queue_open == null ? "—" : fmtNum(del.queue_open)}</div>
              <p className="t-mute text-[13px]">в очереди</p>
            </div>
            <div>
              <div className="tabular text-[22px] font-semibold leading-7">{del.dead == null ? "—" : fmtNum(del.dead)}</div>
              <p className="t-mute inline-flex items-center gap-1.5 text-[13px]">
                не удалось <Hint text={DEF.dead_jobs} />
              </p>
            </div>
            <div>
              <div className="tabular text-[22px] font-semibold leading-7">{fmtNum(del.activations_pending)}</div>
              <p className="t-mute inline-flex items-center gap-1.5 text-[13px]">
                ждут активации <Hint text={DEF.activations} />
              </p>
            </div>
          </div>
          <div className="mt-4">
            <ReasonList reasons={del.reasons} empty="Всё оплаченное выдано." limit={3} />
          </div>
        </Surface>
      </Bento>
    </div>
  );
}
