/**
 * Money — revenue first, then the figures behind it as one list, the
 * trend, providers and the live payments feed; the wallet, refunds,
 * obligations, buying patterns and cohort LTV below. One definition for
 * every figure: database/revenue.py via /metrics/money and /metrics/cohorts.
 */
import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CLASS_LABEL,
  PRODUCT_LABEL,
  PROVIDER_LABEL,
  metricsApi,
  type CohortReport,
  type ProviderPerf,
} from "@/lib/metricsApi";
import { fmtAxisKop, fmtCompactKop, fmtDay, fmtKop, fmtMonth, fmtNum, fmtPct } from "@/lib/format";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Hint } from "@/components/ui/Hint";
import { DeltaPill, ListRow, Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { BarsChart, ShareList, TrendChart } from "@/components/ui/charts";
import { PaymentsFeed } from "@/components/PaymentsFeed";
import { endpoints } from "@/lib/api";
import { EmptyState, ErrorState, LoadingTiles, Skeleton } from "@/components/ui/states";
import { DEF } from "@/lib/metricDefs";
import { WINDOWS } from "@/lib/periods";

type Unit = "day" | "week" | "month";
const UNITS = [
  { value: "day" as Unit, label: "Дни" },
  { value: "week" as Unit, label: "Недели" },
  { value: "month" as Unit, label: "Месяцы" },
];
const OUTSIDE_NET = new Set(["shop", "proxy", "game", "other"]);
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function rateTone(rate: number | null): Tone {
  if (rate == null) return "idle";
  return rate >= 80 ? "ok" : rate >= 50 ? "warn" : "err";
}

function Titled({ children, hint }: { children: ReactNode; hint: string }) {
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5">
      <span className="truncate">{children}</span>
      <Hint text={hint} label={typeof children === "string" ? `Как считается: ${children}` : undefined} />
    </span>
  );
}

/** One figure as a table row: title with ⓘ, a note, the change. */
function Figure({ label, hint, value, meta, delta }: { label: string; hint: string; value: ReactNode; meta?: ReactNode; delta?: number | null }) {
  return (
    <li>
      <ListRow
        title={<Titled hint={hint}>{label}</Titled>}
        meta={
          meta || delta !== undefined ? (
            <span className="inline-flex max-w-full items-baseline gap-1.5">
              {meta && <span className="truncate">{meta}</span>}
              {delta !== undefined && <DeltaPill pct={delta} />}
            </span>
          ) : undefined
        }
        value={value}
      />
    </li>
  );
}

function ProviderRow({ p }: { p: ProviderPerf }) {
  return (
    <ListRow
      leading={<StatusDot tone={rateTone(p.success_rate)} label={`Успешность ${fmtPct(p.success_rate)}`} />}
      title={PROVIDER_LABEL[p.provider] ?? p.provider}
      meta={
        <span className="whitespace-normal">
          Счетов {fmtNum(p.created)}: оплачено {fmtNum(p.paid)}, без оплаты {fmtNum(p.expired)} (брошено {fmtNum(p.abandoned)}).
          Конверсия {fmtPct(p.conversion)}. {fmtKop(p.kopecks)}.
        </span>
      }
      value={fmtPct(p.success_rate)}
    />
  );
}

function CohortTable({ data }: { data: CohortReport }) {
  const max = Math.max(1, ...data.cohorts.flatMap((c) => c.ltv_kopecks.filter((v): v is number => v != null)));
  if (!data.cohorts.length) return <EmptyState title="Пока нет платящих когорт" />;
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[760px] border-separate border-spacing-1 text-[12px]">
        <caption className="sr-only">Накопленная выручка на платящего по месяцам жизни когорты</caption>
        <thead>
          <tr className="t-mute text-left">
            <th scope="col" className="px-2 py-1 font-medium">Когорта</th>
            <th scope="col" className="px-2 py-1 text-right font-medium">Платящих</th>
            {Array.from({ length: data.months }, (_, i) => (
              <th key={i} scope="col" className="px-2 py-1 text-right font-medium">
                М{i}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.cohorts.map((c) => (
            <tr key={c.cohort}>
              <th scope="row" className="whitespace-nowrap px-2 py-1.5 text-left font-medium">
                {fmtMonth(c.cohort)}
              </th>
              <td className="tabular px-2 py-1.5 text-right">{fmtNum(c.payers)}</td>
              {c.ltv_kopecks.map((v, i) => (
                <td
                  key={i}
                  className="tabular rounded-[6px] px-2 py-1.5 text-right"
                  style={v == null ? undefined : { background: `rgb(var(--c-accent) / ${0.05 + (v / max) * 0.25})` }}
                >
                  {v == null ? "" : fmtCompactKop(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Money() {
  const [days, setDays] = useState(30);
  const [unit, setUnit] = useState<Unit>("day");
  const q = useQuery({
    queryKey: ["m-money", days],
    queryFn: () => metricsApi.money(days),
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const cohorts = useQuery({ queryKey: ["m-cohorts", 12], queryFn: () => metricsApi.cohorts(12) });
  const hourly = useQuery({ queryKey: ["stats-hourly", days], queryFn: () => endpoints.statsHourly(Math.min(days, 90)) });
  const d = q.data;

  const chartData = useMemo(() => {
    if (!d) return [];
    const src = unit === "day" ? d.series : unit === "week" ? d.weekly : d.monthly;
    return src.map((p) => ({ date: p.date, net: p.kopecks }));
  }, [d, unit]);

  const header = (
    <PageHeader
      title="Деньги"
      sub="Выручка VPN — деньги, пришедшие извне, до комиссий провайдеров. Магазин, прокси и игра — отдельные строки, вместе с VPN они дают оборот."
      actions={<Segmented label="Период" value={days} options={WINDOWS} onChange={setDays} full className="lg:w-[340px]" />}
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

  const c = d.current;
  const period = `к прошлым ${days} дн.`;
  const classRows = Object.entries(c.by_class)
    .map(([k, v]) => ({
      key: k,
      label: CLASS_LABEL[k] ?? k,
      value: v.kopecks,
      note: OUTSIDE_NET.has(k) ? "вне выручки" : undefined,
      highlight: k === "subscription",
    }))
    .sort((a, b) => b.value - a.value);
  const productRows = Object.entries(c.by_product)
    .map(([k, v]) => ({
      key: k,
      label: PRODUCT_LABEL[k] ?? k,
      value: v.kopecks,
      note: `${fmtNum(v.count)} шт.`,
    }))
    .sort((a, b) => b.value - a.value);
  const xFormat = unit === "month" ? fmtMonth : fmtDay;
  // Average per weekday, not a sum: a 30-day window holds five Mondays
  // and four Tuesdays. Parsed as UTC so the browser zone cannot shift a
  // date the server already bucketed by Moscow day.
  const weekday = WEEKDAYS.map((label, i) => {
    const pts = d.series.filter((p) => (new Date(`${p.date}T00:00:00Z`).getUTCDay() + 6) % 7 === i);
    return { label, avg: pts.length ? pts.reduce((s, p) => s + p.kopecks, 0) / pts.length : 0 };
  });
  const hours = (hourly.data?.series ?? []).map((h) => ({ hour: `${h.hour}:00`, kopecks: Math.round(h.revenue_rubles * 100) }));

  return (
    <div className={q.isFetching ? "opacity-80 transition-opacity" : "transition-opacity"}>
      {header}
      {q.isError && <ErrorState className="mb-3" error={q.error} onRetry={() => q.refetch()} />}
      <Bento>
        <KpiTile
          className="sm:col-span-6 xl:col-span-6"
          size="hero"
          label={`Выручка за ${days} дн.`}
          value={fmtKop(c.net_kopecks)}
          sub={`${fmtNum(c.net_count)} оплат, средний чек ${fmtKop(c.avg_check_kopecks)}. Сегодня ${fmtKop(d.today.net_kopecks)}.`}
          delta={d.delta_pct.net}
          deltaPeriod={period}
          trend={d.series.map((p) => p.kopecks)}
          hint={DEF.revenue}
        />

        <Surface className="sm:col-span-6 xl:col-span-6 xl:row-span-2" label="Показатели" aside={<span className="t-mute text-[13px]">{period}</span>}>
          <ul>
            <Figure
              label="Оборот"
              hint={DEF.gross}
              value={fmtCompactKop(c.gross_kopecks)}
              meta={`VPN ${fmtCompactKop(c.vpn_kopecks)}, магазин ${fmtCompactKop(c.shop_kopecks)}, прокси ${fmtCompactKop(c.proxy_kopecks)}, игра ${fmtCompactKop(c.game_kopecks)}`}
              delta={d.delta_pct.gross}
            />
            <Figure
              label="Платящие"
              hint={DEF.payers}
              value={fmtNum(d.payers.payers)}
              meta={`новых ${fmtNum(d.payers.new)}, вернулись ${fmtNum(d.payers.returning)}`}
              delta={d.delta_pct.payers}
            />
            <Figure label="Средний чек" hint={DEF.avg_check} value={fmtKop(c.avg_check_kopecks)} delta={d.delta_pct.avg_check} />
            <Figure label="ARPPU" hint={DEF.arppu} value={fmtKop(d.arppu_kopecks)} meta="на платящего" />
            <Figure label="ARPU" hint={DEF.arpu} value={fmtKop(d.arpu_kopecks)} meta="на пользователя" />
            <Figure label="MRR" hint={DEF.mrr} value={fmtCompactKop(d.mrr.mrr_kopecks)} meta={`${fmtNum(d.mrr.active_subscriptions)} подписок, сейчас`} />
            <Figure label="Трафик продан" hint={DEF.traffic_sold} value={`${fmtNum(c.traffic_gb_sold)} ГБ`} meta={`${fmtNum(c.traffic_packs_sold)} пакетов`} />
            <Figure
              label="Telegram Stars"
              hint={DEF.stars}
              value={fmtCompactKop(c.stars.kopecks)}
              meta={`${fmtNum(c.stars.count)} оплат, по рублёвой цене: курс звезды не утверждён`}
            />
          </ul>
        </Surface>

        <Surface
          className="sm:col-span-6 xl:col-span-6"
          label="Динамика выручки"
          hint={DEF.revenue}
          aside={<Segmented label="Шаг графика" value={unit} options={UNITS} onChange={setUnit} />}
        >
          <TrendChart
            data={chartData}
            x="date"
            series={[{ key: "net", label: "Выручка", highlight: true }]}
            format={(v) => fmtKop(v)}
            yFormat={fmtAxisKop}
            xFormat={xFormat}
            height={220}
          />
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-6" label="Платёжные провайдеры" hint={DEF.provider_success}>
          {d.providers.length === 0 ? (
            <EmptyState title="За период не было счетов" />
          ) : (
            <ul>
              {d.providers.map((p) => (
                <li key={p.provider}>
                  <ProviderRow p={p} />
                </li>
              ))}
            </ul>
          )}
          <p className="t-mute mt-2 text-[12px] leading-4">
            Справа — успешность: оплачено ÷ (оплачено + закончившиеся без оплаты). Брошенные — счета, всё ещё
            «pending» после срока жизни. Конверсия — оплачено ÷ все счета, открытые за период.
          </p>
        </Surface>

        <PaymentsFeed className="sm:col-span-6 xl:col-span-6" />

        <Surface className="sm:col-span-6 xl:col-span-6" label="Откуда деньги" hint={DEF.gross}>
          <ShareList rows={classRows} format={fmtKop} />
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-6" label="Продукты" hint={DEF.gross}>
          <ShareList rows={productRows} format={fmtKop} />
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-6" label="Баланс и обязательства">
          <ul>
            <Figure
              label="Покупки с баланса"
              hint={DEF.balance_spend}
              value={fmtCompactKop(d.balance_spend.kopecks)}
              meta={`${fmtNum(d.balance_spend.count)} покупок, автопродлений ${fmtNum(d.balance_spend.auto_renew.count)}; не выручка`}
            />
            <Figure
              label="Возвраты и чарджбэки"
              hint={DEF.refunds}
              value={d.refunds.recorded ? fmtNum(d.refunds.count) : "нет данных"}
              meta={d.refunds.count ? `на ${fmtKop(d.refunds.kopecks)}` : "бот пока не записывает возвраты провайдеров"}
            />
            <Figure
              label="Реферальные выплаты"
              hint={DEF.referral_payouts}
              value={fmtCompactKop(d.referral_payouts.kopecks)}
              meta={`${fmtNum(d.referral_payouts.count)} начислений, ${fmtNum(d.referral_payouts.referrers)} партнёров`}
            />
            <Figure
              label="Обязательства"
              hint={DEF.liabilities}
              value={fmtCompactKop(d.liabilities.balance_kopecks)}
              meta={`балансы ${fmtNum(d.liabilities.users_with_balance)} пользователей, сейчас`}
            />
          </ul>
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-6" label="Когда покупают: дни недели" hint={DEF.by_weekday}>
          <BarsChart data={weekday} x="label" y="avg" label="Средняя выручка" format={fmtKop} height={200} />
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-12" label="Когда покупают: часы по Москве" hint={DEF.by_hour}>
          {hourly.isError ? (
            <ErrorState error={hourly.error} onRetry={() => hourly.refetch()} />
          ) : !hourly.data ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <BarsChart data={hours} x="hour" y="kopecks" label="Выручка за период" format={fmtKop} height={200} />
          )}
        </Surface>

        <Surface
          className="sm:col-span-6 xl:col-span-12"
          label="LTV по когортам"
          hint={DEF.cohort_ltv}
          aside={
            cohorts.data && (
              <span className="t-mute text-[13px]">В среднем {fmtKop(cohorts.data.avg_ltv_kopecks)} на платящего</span>
            )
          }
        >
          <p className="t-mute mb-3 max-w-[72ch] text-[13px] leading-[18px]">
            Когорта — месяц первой оплаты. В клетке накопленная выручка на одного платящего к этому месяцу жизни.
          </p>
          {cohorts.isError ? (
            <ErrorState error={cohorts.error} onRetry={() => cohorts.refetch()} />
          ) : cohorts.data ? (
            <CohortTable data={cohorts.data} />
          ) : (
            <Skeleton className="h-48 w-full" />
          )}
        </Surface>
      </Bento>
    </div>
  );
}
