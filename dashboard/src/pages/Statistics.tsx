import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCcw } from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { Bento, PageHeader, SectionHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { ListRow, Segmented, type SegmentedOption } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

/**
 * Statistics hub — consolidated view всех метрик которые мы
 * собираем: платежи (breakdown), рассылки (top-конверсия),
 * рефералы (top-партнёры + КПИ), магазин по продукту.
 *
 * В отличие от Dashboard.tsx (все секции в Collapsible'ах),
 * здесь всё раскрыто по умолчанию — scrolling wall of stats
 * для read-only обзора.
 */
type Hours = 24 | 168 | 720;
const label = (h: number) => (h === 24 ? "24 часа" : h === 168 ? "7 дней" : "30 дней");
const HOURS: SegmentedOption<Hours>[] = ([24, 168, 720] as const).map((h) => ({ value: h, label: label(h) }));

export function Statistics() {
  const [hours, setHours] = useState<Hours>(168);

  const breakdown = useQuery({
    queryKey: ["statistics", "payments-breakdown", hours],
    queryFn: () => endpoints.paymentsBreakdown(hours),
    refetchInterval: 60_000,
  });
  const referrals = useQuery({
    queryKey: ["statistics", "referrals-overall"],
    queryFn: endpoints.referralsOverall,
    refetchInterval: 90_000,
  });
  const topReferrers = useQuery({
    queryKey: ["statistics", "top-referrers", 10],
    queryFn: () =>
      endpoints.referralsTop({
        sort_by: "total_revenue",
        sort_order: "DESC",
        limit: 10,
        offset: 0,
      }),
    refetchInterval: 90_000,
  });
  const broadcasts = useQuery({
    queryKey: ["statistics", "broadcasts-recent", 20],
    queryFn: () =>
      endpoints.broadcastsRecent(20) as Promise<
        Array<Record<string, unknown>>
      >,
    refetchInterval: 30_000,
  });

  const refetchAll = () => {
    breakdown.refetch();
    referrals.refetch();
    topReferrers.refetch();
    broadcasts.refetch();
  };

  const anyLoading =
    breakdown.isFetching ||
    referrals.isFetching ||
    topReferrers.isFetching ||
    broadcasts.isFetching;

  const total = breakdown.data?.total;

  return (
    <div>
      <PageHeader
        title="Статистика"
        sub="Полный срез: платежи по продуктам, топ-партнёры рефералки, последние рассылки с конверсией, магазин по позициям. Обновляется автоматически каждую минуту."
        actions={
          <>
            <Segmented label="Период" value={hours} options={HOURS} onChange={setHours} />
            <button
              type="button"
              onClick={refetchAll}
              className="btn-secondary"
              disabled={anyLoading}
            >
              {anyLoading ? <Spinner /> : <RefreshCcw className="h-3.5 w-3.5" />}
              Обновить
            </button>
          </>
        }
      />

      {/* Total revenue KPI */}
      <SectionHeader title="Общий оборот" sub={`За ${label(hours).toLowerCase()}`} />
      {breakdown.isError ? (
        <ErrorState error={breakdown.error} onRetry={() => breakdown.refetch()} />
      ) : (
        <Bento>
          <KpiTile
            className="sm:col-span-3 xl:col-span-3"
            label="Оплат"
            value={fmtNum(total?.count ?? 0)}
            loading={breakdown.isLoading}
          />
          <KpiTile
            className="sm:col-span-3 xl:col-span-3"
            variant="accent"
            label="Выручка"
            value={fmtRub(total?.revenue_rubles ?? 0)}
            loading={breakdown.isLoading}
          />
          <KpiTile
            className="sm:col-span-3 xl:col-span-3"
            variant="raised"
            label="Средний чек"
            value={fmtRub(total?.count ? total.revenue_rubles / total.count : 0)}
            loading={breakdown.isLoading}
          />
          <KpiTile
            className="sm:col-span-3 xl:col-span-3"
            variant="raised"
            label="Продуктов в наличии"
            value={fmtNum(breakdown.data?.by_type.length ?? 0)}
            loading={breakdown.isLoading}
          />
        </Bento>
      )}

      {/* Payments by product / provider / tariff / apple-nominal */}
      <SectionHeader title="Магазин · разбивка" sub="Что купили, как оплатили, Apple-номиналы" />
      {breakdown.isLoading ? (
        <Bento>
          {[0, 1].map((i) => (
            <Surface key={i} className="sm:col-span-6 xl:col-span-6">
              <SkeletonLines lines={4} />
            </Surface>
          ))}
        </Bento>
      ) : breakdown.isError ? (
        <ErrorState error={breakdown.error} onRetry={() => breakdown.refetch()} />
      ) : !breakdown.data || breakdown.data.total.count === 0 ? (
        <Surface>
          <EmptyState title="За период оплат не было." />
        </Surface>
      ) : (
        <Bento>
          <BreakdownTable
            title="По продукту"
            rows={breakdown.data.by_type.map((r) => ({
              label: PT_LABEL[r.purchase_type] ?? r.purchase_type,
              count: r.count,
              revenue: r.revenue_rubles,
            }))}
          />
          <BreakdownTable
            title="По провайдеру"
            variant="raised"
            rows={breakdown.data.by_provider.map((r) => ({
              label: PROVIDER_LABEL[r.provider] ?? r.provider,
              count: r.count,
              revenue: r.revenue_rubles,
            }))}
          />
          <BreakdownTable
            title="Топ-15 тарифов"
            variant="raised"
            rows={breakdown.data.by_tariff.map((r) => ({
              label: r.tariff,
              count: r.count,
              revenue: r.revenue_rubles,
            }))}
          />
          {breakdown.data.by_apple_nominal.length > 0 && (
            <BreakdownTable
              title="Apple ID · по номиналу"
              rows={breakdown.data.by_apple_nominal.map((r) => ({
                label: `${APPLE_REGION[r.region] ?? r.region} · ${r.nominal}${APPLE_CUR[r.region] ?? "$"}`,
                count: r.count,
                revenue: r.revenue_rubles,
              }))}
            />
          )}
        </Bento>
      )}

      {/* Referrals */}
      <SectionHeader title="Рефералы · сводка" sub="Общая выручка и топ-10 партнёров" />
      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          label="Приглашённых"
          value={fmtNum(asNum(referrals.data?.referred_users_count) ?? 0)}
          loading={referrals.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          label="Активных"
          value={fmtNum(asNum(referrals.data?.active_referrals) ?? 0)}
          loading={referrals.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="steel"
          label="Выручка от рефералов"
          value={fmtRub(asNum(referrals.data?.referral_revenue) ?? 0)}
          loading={referrals.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="raised"
          label="Выплачено кэшбэком"
          value={fmtRub(asNum(referrals.data?.cashback_paid) ?? 0)}
          loading={referrals.isLoading}
        />
        {topReferrers.isLoading ? (
          <Surface className="sm:col-span-6 xl:col-span-12" label="Топ-10 партнёров по выручке">
            <SkeletonLines lines={4} />
          </Surface>
        ) : (
          topReferrers.data &&
          topReferrers.data.length > 0 && (
            <Surface className="sm:col-span-6 xl:col-span-12" label="Топ-10 партнёров по выручке">
              <ol className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                {topReferrers.data.slice(0, 10).map((r, i) => {
                  const id =
                    asNum((r as { referrer_id?: unknown }).referrer_id) ??
                    asNum((r as { telegram_id?: unknown }).telegram_id) ??
                    0;
                  const username =
                    ((r as { username?: string }).username as string) || "—";
                  const invited = asNum((r as { invited_count?: unknown }).invited_count) ?? 0;
                  const trials = asNum((r as { trial_count?: unknown }).trial_count) ?? 0;
                  const paid = asNum((r as { paid_count?: unknown }).paid_count) ?? 0;
                  const revenue =
                    asNum((r as { total_invited_revenue?: unknown }).total_invited_revenue) ??
                    asNum((r as { total_revenue?: unknown }).total_revenue) ??
                    0;
                  return (
                    <li key={id + "_" + i}>
                      <ListRow
                        leading={<span className="t-mute tabular text-[12px]">{i + 1}</span>}
                        title={username !== "—" ? `@${username}` : `tg:${id}`}
                        meta={`Пригласил ${fmtNum(invited)} · триалов ${fmtNum(trials)} · оплатили ${fmtNum(paid)}`}
                        value={fmtRub(revenue)}
                        className="pr-4"
                      />
                    </li>
                  );
                })}
              </ol>
            </Surface>
          )
        )}
      </Bento>

      {/* Broadcasts + segments */}
      <SectionHeader title="Рассылки и аудитория" />
      <Bento>
        <Surface className="sm:col-span-6 xl:col-span-7" label="Последние рассылки">
          <p className="t-mute -mt-1 mb-3 text-[12px]">Всего получателей · доставлено · ошибок</p>
          {broadcasts.isLoading ? (
            <SkeletonLines lines={5} />
          ) : broadcasts.isError ? (
            <ErrorState error={broadcasts.error} onRetry={() => broadcasts.refetch()} className="bg-tile-3" />
          ) : !broadcasts.data || broadcasts.data.length === 0 ? (
            <EmptyState title="Рассылок ещё не было." />
          ) : (
            <ul className="flex flex-col gap-2">
              {broadcasts.data.slice(0, 20).map((b, i) => (
                <li key={i}>
                  <BroadcastRow row={b} />
                </li>
              ))}
            </ul>
          )}
        </Surface>

        {/* Segments cross-ref */}
        <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Сегменты аудитории">
          <p className="t-mute -mt-1 mb-3 text-[12px]">Откройте на главной для управления</p>
          <SegmentsMini />
        </Surface>
      </Bento>
    </div>
  );
}

function SegmentsMini() {
  const segments = useQuery({
    queryKey: ["statistics", "segments"],
    queryFn: endpoints.broadcastSegments,
    refetchInterval: 120_000,
  });
  // Top-10 сегментов по размеру. Hooks before the early returns: calling
  // useMemo only once data arrived crashed the page ("more hooks").
  const sorted = useMemo(() => {
    return [...(segments.data ?? [])].sort((a, b) => b.count - a.count).slice(0, 10);
  }, [segments.data]);
  if (segments.isLoading) return <SkeletonLines lines={2} />;
  if (segments.isError || !segments.data) return null;
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
      {sorted.map((s) => (
        <li
          key={s.key}
          className="flex min-h-[44px] items-center justify-between gap-3 rounded-row bg-tile-3 px-4 py-2 text-[13px]"
        >
          <span className="t-body min-w-0 truncate">{s.label}</span>
          <span className="tabular flex-none font-semibold">{fmtNum(s.count)}</span>
        </li>
      ))}
    </ul>
  );
}

function BroadcastRow({ row }: { row: Record<string, unknown> }) {
  const id = asNum(row.id) ?? 0;
  const title = String(row.title ?? "Без названия");
  const sent = asNum(row.sent_count) ?? asNum(row.sent) ?? 0;
  const failed = asNum(row.failed_count) ?? asNum(row.failed) ?? 0;
  const total = asNum(row.total_recipients) ?? sent + failed;
  const created = String(row.created_at ?? "").slice(0, 16).replace("T", " ");
  return (
    <a href={`/dashboard/broadcasts?id=${id}`} className="list-row gap-2 pr-3 text-[13px]">
      <span className="t-mute tabular w-10 flex-none text-[12px]">#{id}</span>
      <span className="min-w-0 flex-1 truncate text-[14px] font-medium">{title}</span>
      <span className="t-mute tabular hidden flex-none text-[12px] sm:inline">{created}</span>
      <span className="badge-muted tabular flex-none" title="Получателей">
        {fmtNum(total)}
      </span>
      <span className="badge-success tabular flex-none" title="Доставлено">
        ✓ {fmtNum(sent)}
      </span>
      {failed > 0 && (
        <span className="badge-danger tabular flex-none" title="Ошибок">
          ✕ {fmtNum(failed)}
        </span>
      )}
    </a>
  );
}

// ── Helpers / small components ───────────────────────────────────────

function BreakdownTable({
  title,
  rows,
  variant,
}: {
  title: string;
  rows: Array<{ label: string; count: number; revenue: number }>;
  variant?: "ink" | "raised";
}) {
  const total = rows.reduce((a, r) => a + r.revenue, 0);
  return (
    <Surface className="sm:col-span-6 xl:col-span-6" variant={variant} label={title}>
      {rows.length === 0 && <p className="t-mute text-[13px]">Нет данных</p>}
      <ul className="flex flex-col gap-3">
        {rows.map((r) => {
          const pct = total > 0 ? (r.revenue / total) * 100 : 0;
          return (
            <li key={r.label}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3 text-[13px]">
                <span className="t-body min-w-0 truncate">{r.label}</span>
                <span className="tabular flex-none font-medium">
                  {fmtRub(r.revenue)}
                  <span className="t-mute ml-2 text-[12px] font-normal">{fmtNum(r.count)}</span>
                </span>
              </div>
              <div className="pill-track h-2">
                <div className="pill-fill" style={{ width: `${Math.max(2, pct)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </Surface>
  );
}

function SkeletonLines({ lines }: { lines: number }) {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full rounded-row" />
      ))}
    </div>
  );
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

// ── Локальные словари меток ──────────────────────────────────────────

const PT_LABEL: Record<string, string> = {
  subscription: "Подписка",
  balance_topup: "Пополнение баланса",
  gift: "Подарок",
  telegram_premium: "Telegram Premium",
  telegram_stars: "Telegram Stars",
  traffic_pack: "Пакет ГБ",
  apple_id: "Apple ID",
  steam: "Steam",
  spotify: "Spotify Premium",
  proxy: "MTProxy",
  unknown: "Прочее",
};
const PROVIDER_LABEL: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Telegram Stars",
  wata: "WATA",
  // Historical rows only: Lava was removed, old payments still carry it.
  lava: "Lava",
  balance: "С баланса",
  unknown: "Прочее",
};
const APPLE_REGION: Record<string, string> = {
  usa: "🇺🇸 USA",
  turkey: "🇹🇷 Turkey",
  russia: "🇷🇺 Russia",
  india: "🇮🇳 India",
};
const APPLE_CUR: Record<string, string> = {
  usa: "$",
  turkey: "TL",
  russia: "₽",
  india: "INR",
};
