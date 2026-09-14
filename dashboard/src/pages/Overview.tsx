/**
 * Overview — the owner's home screen on the phone, read top to bottom:
 *   1. Status: «Всё в порядке», or only the real problems (tap → detail).
 *   2. Money: VPN revenue for today / 7 / 30 days, delta, payments, ARPPU.
 *   3. Subscribers: paid, trials, new users, churn.
 *   4. Renewals: what expires in the next 7 days.
 *   5. Payments: each provider with a status dot.
 *   6. Delivery: the provisioning queue, one line while it is empty.
 * Figures come from /metrics/overview and /metrics/payments-health
 * (docs/dashboard/metrics.md); every ⓘ reads lib/metricDefs.
 */
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import {
  KIND_LABEL,
  PROVIDER_LABEL,
  metricsApi,
  type Alert,
  type Kpi,
  type OverviewReport,
  type PaymentsHealth,
  type ProviderHealth,
  type SubKind,
} from "@/lib/metricsApi";
import { DEF } from "@/lib/metricDefs";
import { OVERVIEW_PERIODS, WINDOWS as SHARED_WINDOWS, comparedTo } from "@/lib/periods";
import { fmtKop, fmtNum, fmtPct, fmtRelative } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Bento, PageHeader } from "@/components/ui/Surface";
import { Hint } from "@/components/ui/Hint";
import { DeltaPill, ListRow, Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { ReasonList } from "@/components/ui/StatusBadge";
import { ErrorState, LoadingTiles, Skeleton } from "@/components/ui/states";
import { Sparkline } from "@/components/Sparkline";

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
const PROVIDER_STATE: Record<ProviderHealth["state"], { tone: Tone; text: string }> = {
  ok: { tone: "ok", text: "в ритме" },
  silent: { tone: "warn", text: "молчит" },
  rare: { tone: "idle", text: "мало оплат" },
  disabled: { tone: "idle", text: "выключен" },
};

/** Problems worth the owner's attention: the backend's critical and
    warning alerts (info — e.g. «истекает за 7 дней» — lives in its block). */
export function realProblems(alerts: Alert[]): Alert[] {
  return alerts.filter((a) => a.level === "critical" || a.level === "warning");
}

/** A section: small-caps header outside, cards inside (iOS inset group). */
function Group({
  title,
  hint,
  to,
  className,
  children,
}: {
  title: string;
  hint?: string;
  to?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("min-w-0", className)} aria-label={title}>
      <div className="mb-1.5 flex min-h-[24px] items-end justify-between gap-3 px-4">
        <h2 className="section-h flex items-center gap-1.5">
          {title}
          {hint && <Hint text={hint} label={`Как считается: ${title}`} />}
        </h2>
        {to && (
          <Link to={to} className="tap-target text-[15px] leading-5 text-accent active:opacity-50">
            Подробнее
          </Link>
        )}
      </div>
      {children}
    </section>
  );
}

function Titled({ children, hint }: { children: ReactNode; hint: string }) {
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5">
      <span className="truncate">{children}</span>
      <Hint text={hint} label={typeof children === "string" ? `Как считается: ${children}` : undefined} />
    </span>
  );
}

/** «было 12 300 ₽ · ↑ 4,1 %» under a row title. */
function Was({ prev, delta, higherIsBetter }: { prev: string; delta: number | null; higherIsBetter?: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span>было {prev}</span>
      {delta != null && <DeltaPill pct={delta} higherIsBetter={higherIsBetter} />}
    </span>
  );
}

/** A row that expands the rows below it. Not a <button> around the
    title: the title carries its own ⓘ button, and buttons cannot nest.
    The chevron is the keyboard control; tapping the row works too. */
function DisclosureRow({
  open,
  onToggle,
  title,
  meta,
  value,
  what,
}: {
  open: boolean;
  onToggle: () => void;
  title: ReactNode;
  meta: ReactNode;
  value: ReactNode;
  what: string;
}) {
  return (
    <div className="list-row cursor-pointer" onClick={onToggle}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[17px] leading-[22px] lg:text-[15px] lg:leading-5">{title}</span>
        <span className="t-mute mt-0.5 block truncate text-[13px] leading-[18px]">{meta}</span>
      </span>
      <span className="tabular flex-none text-right text-[17px] leading-[22px] text-body lg:text-[15px] lg:leading-5">{value}</span>
      <button
        type="button"
        className="tap-target -mr-1 grid h-6 w-6 flex-none place-items-center"
        aria-expanded={open}
        aria-label={open ? `Свернуть: ${what}` : `Показать: ${what}`}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
      >
        <ChevronDown
          className={cn("row-chevron h-[18px] w-[18px] transition-transform", open && "rotate-180")}
          strokeWidth={2.2}
          aria-hidden="true"
        />
      </button>
    </div>
  );
}

function kpiFmt(k: Kpi, v: number): string {
  return k.unit === "kopecks" ? fmtKop(v) : fmtNum(v);
}

// ── 1. Status ────────────────────────────────────────────────────────

function nodesText(p: OverviewReport["panel"]): string | null {
  if (!p.checked || !p.available || p.nodes_online == null) return null;
  const enabled = p.nodes_enabled ?? p.nodes_total;
  const disabled = p.nodes_disabled ? `, ${fmtNum(p.nodes_disabled)} выкл.` : "";
  return `ноды ${fmtNum(p.nodes_online)} из ${fmtNum(enabled)}${disabled}`;
}

function StatusCard({ d }: { d: OverviewReport }) {
  const problems = realProblems(d.alerts);
  const facts = [
    d.panel.online_now != null ? `онлайн ${fmtNum(d.panel.online_now)}` : null,
    nodesText(d.panel),
    d.health.checked_at ? `проверено ${fmtRelative(d.health.checked_at)}` : null,
  ].filter(Boolean);

  if (problems.length === 0) {
    const unknown = d.health.status === "unknown";
    return (
      <Link to="/health" className="tile flex items-center gap-3 p-4 active:opacity-60" aria-label="Статус: открыть «Здоровье»">
        {unknown ? (
          <span className="grid h-7 w-7 flex-none place-items-center">
            <StatusDot tone="idle" />
          </span>
        ) : (
          <CheckCircle2 className="h-7 w-7 flex-none text-success" strokeWidth={2} aria-hidden="true" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block text-[17px] font-semibold leading-[22px]">
            {unknown ? "Нет данных о системе" : "Всё в порядке"}
          </span>
          <span className="t-mute mt-0.5 block text-[13px] leading-[18px]">
            {unknown
              ? "Проверка системы не завершилась — откройте «Здоровье»."
              : `Система, платежи, выдача и панель без проблем${facts.length ? ` · ${facts.join(" · ")}` : ""}`}
          </span>
        </span>
        <ChevronRight className="row-chevron h-5 w-5" strokeWidth={2.2} aria-hidden="true" />
      </Link>
    );
  }

  const critical = problems.some((a) => a.level === "critical");
  return (
    <section className="tile p-4" aria-label="Требует внимания">
      <div className="mb-1 flex items-center gap-2.5">
        <AlertTriangle className={cn("h-6 w-6 flex-none", critical ? "text-danger" : "text-warning")} strokeWidth={2} aria-hidden="true" />
        <h2 className="text-[17px] font-semibold leading-[22px]">
          {critical ? "Есть сбой" : "Требует внимания"}
          <span className="t-mute font-normal"> · {fmtNum(problems.length)}</span>
        </h2>
      </div>
      <ul>
        {problems.slice(0, 6).map((a) => (
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
      {problems.length > 6 && <p className="t-mute pt-2 text-[13px]">И ещё {fmtNum(problems.length - 6)} — на экране «Здоровье».</p>}
      {facts.length > 0 && <p className="t-mute mt-2 text-[13px] leading-[18px]">{facts.join(" · ")}</p>}
    </section>
  );
}

// ── 2. Money ─────────────────────────────────────────────────────────

function MoneyCard({ d, days }: { d: OverviewReport; days: number }) {
  const [open, setOpen] = useState(false);
  const k = Object.fromEntries(d.kpis.map((x) => [x.key, x])) as Record<string, Kpi | undefined>;
  const rev = k.revenue;
  const rows: { kpi: Kpi | undefined; label: string; hint: string }[] = [
    { kpi: k.payments, label: "Оплат", hint: DEF.payments },
    { kpi: k.arppu, label: "ARPPU", hint: DEF.arppu },
    { kpi: k.payers, label: "Платящие", hint: DEF.payers },
    { kpi: k.new_payers, label: "Новые платящие", hint: DEF.new_payers },
  ];
  return (
    <div className="tile p-4">
      {rev && (
        <div className="flex items-end justify-between gap-4 pb-3">
          <div className="min-w-0">
            <p className="t-mute text-[13px] leading-[18px]">
              {days === 1 ? "Сегодня, до этой минуты" : `За ${days} дн.`}
            </p>
            <div className="tabular mt-0.5 text-[34px] font-bold leading-[41px] track-hero">{fmtKop(rev.value)}</div>
            <div className="mt-1">
              <DeltaPill pct={rev.delta_pct} period={comparedTo(days)} />
            </div>
            <p className="t-mute mt-0.5 text-[13px] leading-[18px]">было {fmtKop(rev.prev)}</p>
          </div>
          {d.series.length > 1 && (
            <div className="w-[38%] max-w-[220px] flex-none pb-1">
              <Sparkline data={d.series.map((p) => p.kopecks)} height={48} color="rgb(var(--c-accent))" showEndDot />
              <p className="t-mute mt-1 text-right text-[11px] leading-[13px]">по дням, {fmtNum(d.series.length)} дн.</p>
            </div>
          )}
        </div>
      )}
      <ul>
        {rows.map(
          ({ kpi, label, hint }) =>
            kpi && (
              <li key={kpi.key}>
                <ListRow
                  title={<Titled hint={hint}>{label}</Titled>}
                  meta={<Was prev={kpiFmt(kpi, kpi.prev)} delta={kpi.delta_pct} />}
                  value={kpiFmt(kpi, kpi.value)}
                />
              </li>
            ),
        )}
        <li>
          <DisclosureRow
            open={open}
            onToggle={() => setOpen((v) => !v)}
            what="магазин, прокси и игра"
            title={<Titled hint={DEF.gross}>Оборот</Titled>}
            meta={open ? "VPN + магазин + прокси + игра" : "магазин, прокси и игра — отдельно от выручки VPN"}
            value={fmtKop(d.lines.gross.value)}
          />
        </li>
        {open &&
          LINES.map((l) => (
            <li key={l.key}>
              <ListRow
                className="pl-4"
                title={<Titled hint={l.def}>{l.label}</Titled>}
                meta={<Was prev={fmtKop(d.lines[l.key].prev)} delta={d.lines[l.key].delta_pct} />}
                value={fmtKop(d.lines[l.key].value)}
              />
            </li>
          ))}
      </ul>
    </div>
  );
}

// ── 3. Subscribers ───────────────────────────────────────────────────

function SubscribersCard({ d, days }: { d: OverviewReport; days: number }) {
  const [open, setOpen] = useState(false);
  const s = d.subscribers;
  const nu = d.kpis.find((x) => x.key === "new_users");
  const grantedMeta = Object.entries(s.granted_sources)
    .sort((a, b) => b[1] - a[1])
    .map(([src, n]) => `${SOURCE_LABEL[src] ?? src} ${fmtNum(n)}`)
    .join(", ");
  return (
    <div className="tile p-4 py-1">
      <ul>
        <li>
          <ListRow
            title={<Titled hint={DEF.paid_subs}>Платные активные</Titled>}
            meta={`с доступом ${fmtNum(s.with_access)} · автопродление ${fmtPct(s.auto_renew_share)}`}
            value={fmtNum(s.paid)}
          />
        </li>
        <li>
          <ListRow title={<Titled hint={DEF.trial_subs}>Пробные</Titled>} value={fmtNum(s.by_kind.trial ?? 0)} />
        </li>
        {nu && (
          <li>
            <ListRow
              title={<Titled hint={DEF.new_users}>{days === 1 ? "Новые сегодня" : `Новые за ${days} дн.`}</Titled>}
              meta={<Was prev={fmtNum(nu.prev)} delta={nu.delta_pct} />}
              value={fmtNum(nu.value)}
            />
          </li>
        )}
        <li>
          <ListRow
            title={<Titled hint={DEF.churn}>Отток</Titled>}
            meta={
              <span className="inline-flex items-center gap-1.5">
                продлили {fmtPct(s.renewal_rate)} <Hint text={DEF.renewal_rate} label="Как считается: продления" />
              </span>
            }
            value={fmtPct(s.churn_rate)}
          />
        </li>
        <li>
          <DisclosureRow
            open={open}
            onToggle={() => setOpen((v) => !v)}
            what="виды доступа"
            title={<Titled hint={DEF.with_access}>С доступом</Titled>}
            meta={open ? "по видам подписки" : "платные, пробные, выданные, подарки, обход"}
            value={fmtNum(s.with_access)}
          />
        </li>
        {open &&
          KIND_ORDER.map((key) => (
            <li key={key}>
              <ListRow
                className="pl-4"
                title={key === "granted" ? <Titled hint={DEF.granted}>{KIND_LABEL[key]}</Titled> : key === "bypass_only" ? <Titled hint={DEF.bypass_only}>{KIND_LABEL[key]}</Titled> : KIND_LABEL[key]}
                meta={key === "granted" && grantedMeta ? grantedMeta : undefined}
                value={fmtNum(s.by_kind[key] ?? 0)}
              />
            </li>
          ))}
      </ul>
    </div>
  );
}

// ── 4. Renewals ──────────────────────────────────────────────────────

const BAR_H = 64;

function DayBars({ days }: { days: { date: string; total: number; auto_renew: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.total));
  return (
    <div className="flex gap-1.5" role="img" aria-label="Истекает по дням, из них с автопродлением">
      {days.map((d) => {
        const wd = new Date(`${d.date}T12:00:00Z`).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "Europe/Moscow" });
        return (
          <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1" title={`${d.date}: ${d.total}, автопродление ${d.auto_renew}`}>
            <span className="tabular t-mute text-[11px] leading-3">{d.total ? fmtNum(d.total) : ""}</span>
            {/* Pixel heights: WebKit does not resolve % heights inside flex-grown items. */}
            <div className="flex w-full flex-col justify-end overflow-hidden rounded-[5px] bg-[rgb(var(--c-fill)/var(--a-fill))]" style={{ height: BAR_H }}>
              <div className="w-full bg-[rgb(var(--c-ink)/0.22)]" style={{ height: Math.round(((d.total - d.auto_renew) / max) * BAR_H) }} />
              <div className="w-full bg-accent" style={{ height: Math.round((d.auto_renew / max) * BAR_H) }} />
            </div>
            <span className="t-mute text-[11px] leading-3">{wd}</span>
          </div>
        );
      })}
    </div>
  );
}

function PipelineCard({ pipe }: { pipe: OverviewReport["subscribers"]["pipeline"] }) {
  if (!pipe) {
    return (
      <div className="tile p-4 text-[15px]">
        Не удалось посчитать.{" "}
        <Link to="/subscribers" className="text-accent">
          Открыть «Подписчики»
        </Link>
      </div>
    );
  }
  return (
    <div className="tile p-4">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div>
          <div className="tabular text-[28px] font-bold leading-[34px]">{fmtNum(pipe.expiring)}</div>
          <p className="t-mute text-[13px] leading-[18px]">подписок истекает</p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <div className="tabular text-[17px] font-semibold leading-[22px]">{fmtNum(pipe.auto_renew)}</div>
            <p className="t-mute text-[13px] leading-[18px]">продлятся сами</p>
          </div>
          <div>
            <div className="tabular text-[17px] font-semibold leading-[22px]">{fmtNum(pipe.manual)}</div>
            <p className="t-mute text-[13px] leading-[18px]">без автопродления</p>
          </div>
        </div>
      </div>
      {pipe.by_day.length > 0 && (
        <div className="mt-4">
          <DayBars days={pipe.by_day} />
          <p className="t-mute mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
            <span className="inline-flex items-center gap-1.5">
              <span className="dot" style={{ background: "rgb(var(--c-accent))" }} aria-hidden="true" /> с автопродлением
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="dot" style={{ background: "rgb(var(--c-ink) / 0.22)" }} aria-hidden="true" /> без
            </span>
          </p>
        </div>
      )}
      <ul className="mt-2">
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
  );
}

// ── 5. Payments per provider ─────────────────────────────────────────

function providerRows(ph: PaymentsHealth) {
  const by24 = new Map(ph.providers_24h.map((p) => [p.provider, p]));
  const keys: string[] = [];
  for (const k of [...ph.providers.map((p) => p.provider), ...by24.keys()]) if (!keys.includes(k)) keys.push(k);
  const health = new Map(ph.providers.map((p) => [p.provider, p]));
  return keys.map((k) => ({ key: k, h: health.get(k), d24: by24.get(k) }));
}

function PaymentsCard({ pay, ph, phError, onRetry }: {
  pay: OverviewReport["payments"];
  ph: PaymentsHealth | undefined;
  phError: unknown;
  onRetry: () => void;
}) {
  const rows = ph ? providerRows(ph) : [];
  return (
    <div className="tile p-4">
      <p className="text-[15px] leading-5">
        За сутки оплачено <span className="tabular font-semibold">{fmtPct(pay.conversion_24h)}</span> счетов
        <span className="t-mute">
          {" "}
          ({fmtNum(pay.paid_24h)} из {fmtNum(pay.invoices_24h)})
        </span>
      </p>
      <ul className="mt-1">
        {ph ? (
          rows.map(({ key, h, d24 }) => {
            const st = h ? PROVIDER_STATE[h.state] : null;
            const tone: Tone = st?.tone ?? (d24 && d24.paid > 0 ? "ok" : "idle");
            return (
              <li key={key}>
                <ListRow
                  leading={<StatusDot tone={tone} label={st?.text ?? "нет данных о ритме"} />}
                  title={PROVIDER_LABEL[key] ?? key}
                  meta={[
                    st?.text,
                    d24 ? `${fmtNum(d24.paid)} из ${fmtNum(d24.created)} за сутки` : null,
                    h?.last_paid_at ? `оплата ${fmtRelative(h.last_paid_at)}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  value={d24 ? fmtPct(d24.conversion) : "—"}
                />
              </li>
            );
          })
        ) : phError ? (
          <li className="py-2">
            <ErrorState error={phError} onRetry={onRetry} className="bg-elev p-3" />
          </li>
        ) : (
          <li className="py-2">
            <Skeleton className="h-24 w-full" />
          </li>
        )}
        <li>
          <ListRow
            leading={<StatusDot tone={pay.errors_24h ? "err" : "ok"} />}
            title={<Titled hint={DEF.errors_24h}>Ошибки платежей за 24 ч</Titled>}
            value={fmtNum(pay.errors_24h)}
            to="/health"
          />
        </li>
      </ul>
      <p className="t-mute mt-2 flex flex-wrap items-center gap-1.5 text-[12px] leading-4">
        Справа — конверсия счетов за сутки. «Молчит» — давно не было оплат
        <Hint text={DEF.silence} label="Как считается: провайдер молчит" />
      </p>
      {pay.reasons.length > 0 && (
        <div className="mt-3">
          <ReasonList reasons={pay.reasons} limit={3} />
        </div>
      )}
    </div>
  );
}

// ── 6. Delivery ──────────────────────────────────────────────────────

function DeliveryCard({ del }: { del: OverviewReport["delivery"] }) {
  const idle = !del.queue_open && !del.dead && !del.activations_pending && del.reasons.length === 0;
  if (idle) {
    return (
      <div className="tile px-4 py-1">
        <ListRow
          to="/health"
          leading={<StatusDot tone={del.status === "unknown" ? "idle" : "ok"} />}
          title={del.status === "unknown" ? "Нет данных об очереди" : "Всё оплаченное выдано"}
          meta={del.queue_open == null ? "очереди выдачи ещё нет" : "очередь пуста, неудачных выдач нет"}
        />
      </div>
    );
  }
  return (
    <div className="tile p-4 py-1">
      <ul>
        <li>
          <ListRow title={<Titled hint={DEF.queue}>В очереди</Titled>} value={del.queue_open == null ? "—" : fmtNum(del.queue_open)} />
        </li>
        <li>
          <ListRow
            leading={<StatusDot tone={del.dead ? "err" : "ok"} />}
            title={<Titled hint={DEF.dead_jobs}>Не удалось выдать</Titled>}
            value={del.dead == null ? "—" : fmtNum(del.dead)}
            to="/health"
          />
        </li>
        <li>
          <ListRow
            leading={<StatusDot tone={del.activations_pending ? "warn" : "ok"} />}
            title={<Titled hint={DEF.activations}>Ждут активации</Titled>}
            value={fmtNum(del.activations_pending)}
          />
        </li>
      </ul>
      {del.reasons.length > 0 && (
        <div className="pb-3 pt-2">
          <ReasonList reasons={del.reasons} limit={3} />
        </div>
      )}
    </div>
  );
}

// ── Screen ───────────────────────────────────────────────────────────

export function Overview() {
  const [days, setDays] = useState(1);
  const q = useQuery({
    queryKey: ["m-overview", days],
    queryFn: () => metricsApi.overview(days),
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const ph = useQuery({ queryKey: ["m-payments-health"], queryFn: metricsApi.paymentsHealth, refetchInterval: 60_000 });
  const d = q.data;
  const header = (
    <PageHeader
      title="Обзор"
      sub={
        <span className="inline-flex flex-wrap items-center gap-1.5">
          Сутки по Москве, сравнение — с таким же отрезком раньше
          <Hint text={DEF.period} label="Как считается: период" />
        </span>
      }
      actions={<Segmented label="Период" value={days} options={OVERVIEW_PERIODS} onChange={setDays} full className="lg:w-[340px]" />}
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

  return (
    <div className={q.isFetching ? "opacity-80 transition-opacity" : "transition-opacity"}>
      {header}
      {q.isError && <ErrorState className="mb-3" error={q.error} onRetry={() => q.refetch()} />}

      <Bento className="gap-y-7">
        <div className="sm:col-span-6 xl:col-span-12">
          <StatusCard d={d} />
        </div>
        <Group title="Деньги" hint={DEF.revenue} to="/money" className="sm:col-span-6 xl:col-span-7">
          <MoneyCard d={d} days={days} />
        </Group>
        <Group title="Подписчики" hint={DEF.with_access} to="/subscribers" className="sm:col-span-6 xl:col-span-5">
          <SubscribersCard d={d} days={days} />
        </Group>
        <Group title="Истекают за 7 дней" hint={DEF.pipeline} to="/subscribers" className="sm:col-span-6 xl:col-span-7">
          <PipelineCard pipe={d.subscribers.pipeline} />
        </Group>
        <Group title="Платежи" hint={DEF.provider_conversion} to="/health" className="sm:col-span-6 xl:col-span-5">
          <PaymentsCard pay={d.payments} ph={ph.data} phError={ph.isError ? ph.error : null} onRetry={() => ph.refetch()} />
        </Group>
        <Group title="Выдача доступа" hint={DEF.queue} to="/health" className="sm:col-span-6 xl:col-span-12">
          <DeliveryCard del={d.delivery} />
        </Group>
      </Bento>
    </div>
  );
}
