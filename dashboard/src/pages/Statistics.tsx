import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  CreditCard,
  Megaphone,
  Percent,
  RefreshCcw,
  ShoppingBag,
  Users,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { Spinner } from "@/components/Spinner";

/**
 * Statistics hub — consolidated view всех метрик которые мы
 * собираем: платежи (breakdown), рассылки (top-конверсия),
 * рефералы (top-партнёры + КПИ), магазин по продукту.
 *
 * В отличие от Dashboard.tsx (все секции в Collapsible'ах),
 * здесь всё раскрыто по умолчанию — scrolling wall of stats
 * для read-only обзора.
 */
export function Statistics() {
  const [hours, setHours] = useState<24 | 168 | 720>(168);
  const label = (h: number) => (h === 24 ? "24 часа" : h === 168 ? "7 дней" : "30 дней");

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

  return (
    <div className="mx-auto max-w-6xl space-y-4 pb-8 pt-2 md:pt-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-fg">
            <BarChart3 className="h-5 w-5 text-fg-muted" />
            Статистика
          </h1>
          <p className="mt-1 max-w-xl text-sm text-fg-muted">
            Полный срез: платежи по продуктам, топ-партнёры рефералки,
            последние рассылки с конверсией, магазин по позициям.
            Обновляется автоматически каждую минуту.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded-lg border border-border">
            {[24, 168, 720].map((h) => (
              <button
                type="button"
                key={h}
                onClick={() => setHours(h as 24 | 168 | 720)}
                className={
                  hours === h
                    ? "bg-accent px-3 py-1.5 text-xs font-semibold text-bg"
                    : "bg-bg-card px-3 py-1.5 text-xs font-medium text-fg-muted hover:bg-bg-subtle/60"
                }
              >
                {label(h)}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={refetchAll}
            className="btn-secondary"
            disabled={anyLoading}
          >
            {anyLoading ? <Spinner /> : <RefreshCcw className="h-3.5 w-3.5" />}
            Обновить
          </button>
        </div>
      </header>

      {/* Total revenue KPI */}
      <SectionCard
        icon={CreditCard}
        title="Общий оборот"
        subtitle={`За ${label(hours).toLowerCase()}`}
      >
        {breakdown.isLoading ? (
          <Skeleton lines={3} />
        ) : breakdown.isError ? (
          <ErrorNote err={breakdown.error} />
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <BigStat
              label="Оплат"
              value={fmtNum(breakdown.data?.total.count ?? 0)}
            />
            <BigStat
              label="Выручка"
              value={fmtRub(breakdown.data?.total.revenue_rubles ?? 0)}
              accent
            />
            <BigStat
              label="Средний чек"
              value={fmtRub(
                breakdown.data?.total.count
                  ? breakdown.data.total.revenue_rubles /
                      breakdown.data.total.count
                  : 0,
              )}
            />
            <BigStat
              label="Продуктов в наличии"
              value={fmtNum(breakdown.data?.by_type.length ?? 0)}
            />
          </div>
        )}
      </SectionCard>

      {/* Payments by product / provider / tariff / apple-nominal */}
      <SectionCard
        icon={ShoppingBag}
        title="Магазин · разбивка"
        subtitle="что купили + как оплатили + Apple-номиналы"
      >
        {breakdown.isLoading ? (
          <Skeleton lines={4} />
        ) : breakdown.isError ? (
          <ErrorNote err={breakdown.error} />
        ) : !breakdown.data || breakdown.data.total.count === 0 ? (
          <EmptyRow text="За период оплат не было." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
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
              rows={breakdown.data.by_provider.map((r) => ({
                label: PROVIDER_LABEL[r.provider] ?? r.provider,
                count: r.count,
                revenue: r.revenue_rubles,
              }))}
            />
            <BreakdownTable
              title="Топ-15 тарифов"
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
          </div>
        )}
      </SectionCard>

      {/* Referrals */}
      <SectionCard
        icon={Percent}
        title="Рефералы · сводка"
        subtitle="общая выручка + топ-10 партнёров"
      >
        {referrals.isLoading || topReferrers.isLoading ? (
          <Skeleton lines={4} />
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <BigStat
                label="Приглашённых"
                value={fmtNum(asNum(referrals.data?.referred_users_count) ?? 0)}
              />
              <BigStat
                label="Активных"
                value={fmtNum(asNum(referrals.data?.active_referrals) ?? 0)}
              />
              <BigStat
                label="Выручка от рефералов"
                value={fmtRub(asNum(referrals.data?.referral_revenue) ?? 0)}
                accent
              />
              <BigStat
                label="Выплачено кэшбэком"
                value={fmtRub(asNum(referrals.data?.cashback_paid) ?? 0)}
              />
            </div>
            {topReferrers.data && topReferrers.data.length > 0 && (
              <div className="rounded-xl border border-border bg-bg-subtle/40 p-3">
                <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
                  Топ-10 партнёров (по выручке)
                </div>
                <div className="space-y-1">
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
                      <div
                        key={id + "_" + i}
                        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-bg-elevated"
                      >
                        <span className="w-5 text-fg-subtle tabular-nums">{i + 1}.</span>
                        <span className="min-w-0 flex-1 truncate font-medium text-fg">
                          {username !== "—" ? `@${username}` : `tg:${id}`}
                        </span>
                        <span className="rounded bg-fg/5 px-1.5 py-0.5 text-[10px] text-fg-muted tabular-nums">
                          👥 {fmtNum(invited)}
                        </span>
                        <span className="rounded bg-info/10 px-1.5 py-0.5 text-[10px] text-info tabular-nums">
                          🎁 {fmtNum(trials)}
                        </span>
                        <span className="rounded bg-success/10 px-1.5 py-0.5 text-[10px] text-success tabular-nums">
                          💳 {fmtNum(paid)}
                        </span>
                        <span className="min-w-[68px] text-right text-xs font-semibold tabular-nums text-fg">
                          {fmtRub(revenue)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </SectionCard>

      {/* Broadcasts */}
      <SectionCard
        icon={Megaphone}
        title="Последние рассылки"
        subtitle="кто отправлено · доставлено · ошибок"
      >
        {broadcasts.isLoading ? (
          <Skeleton lines={5} />
        ) : broadcasts.isError ? (
          <ErrorNote err={broadcasts.error} />
        ) : !broadcasts.data || broadcasts.data.length === 0 ? (
          <EmptyRow text="Рассылок ещё не было." />
        ) : (
          <div className="space-y-1">
            {broadcasts.data.slice(0, 20).map((b, i) => (
              <BroadcastRow key={i} row={b} />
            ))}
          </div>
        )}
      </SectionCard>

      {/* Segments cross-ref */}
      <SectionCard
        icon={Users}
        title="Сегменты аудитории"
        subtitle="откройте на главной для управления"
      >
        <SegmentsMini />
      </SectionCard>
    </div>
  );
}

function SegmentsMini() {
  const segments = useQuery({
    queryKey: ["statistics", "segments"],
    queryFn: endpoints.broadcastSegments,
    refetchInterval: 120_000,
  });
  if (segments.isLoading) return <Skeleton lines={2} />;
  if (segments.isError || !segments.data) return null;
  // Top-10 сегментов по размеру
  const sorted = useMemo(() => {
    return [...(segments.data ?? [])].sort((a, b) => b.count - a.count).slice(0, 10);
  }, [segments.data]);
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {sorted.map((s) => (
        <div
          key={s.key}
          className="flex items-center justify-between rounded-lg border border-border bg-bg-subtle/40 px-3 py-2 text-xs"
        >
          <span className="truncate text-fg-muted">{s.label}</span>
          <span className="ml-2 shrink-0 rounded bg-accent/10 px-1.5 py-0.5 font-semibold text-accent tabular-nums">
            {fmtNum(s.count)}
          </span>
        </div>
      ))}
    </div>
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
    <a
      href={`/dashboard/broadcasts?id=${id}`}
      className="flex items-center gap-3 rounded-lg border border-border/60 bg-bg-card px-3 py-2 text-xs hover:border-accent/40"
    >
      <span className="w-8 shrink-0 text-fg-subtle tabular-nums">#{id}</span>
      <span className="min-w-0 flex-1 truncate font-medium text-fg">{title}</span>
      <span className="hidden shrink-0 text-fg-subtle sm:inline">{created}</span>
      <span className="shrink-0 rounded bg-fg/5 px-1.5 py-0.5 text-fg-muted tabular-nums">
        {fmtNum(total)}
      </span>
      <span className="shrink-0 rounded bg-success/10 px-1.5 py-0.5 text-success tabular-nums">
        ✓ {fmtNum(sent)}
      </span>
      {failed > 0 && (
        <span className="shrink-0 rounded bg-danger/10 px-1.5 py-0.5 text-danger tabular-nums">
          ✕ {fmtNum(failed)}
        </span>
      )}
    </a>
  );
}

// ── Helpers / small components ───────────────────────────────────────

function SectionCard({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: typeof BarChart3;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border bg-bg-card p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
            <Icon className="h-3 w-3" /> {title}
          </div>
          {subtitle && (
            <div className="mt-0.5 text-[11px] text-fg-subtle">{subtitle}</div>
          )}
        </div>
      </div>
      {children}
    </section>
  );
}

function BigStat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/40 p-3">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div
        className={`mt-0.5 text-xl font-semibold tabular-nums ${accent ? "text-accent" : "text-fg"}`}
      >
        {value}
      </div>
    </div>
  );
}

function BreakdownTable({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label: string; count: number; revenue: number }>;
}) {
  const total = rows.reduce((a, r) => a + r.revenue, 0);
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/40 p-3">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {title}
      </div>
      <div className="space-y-1.5">
        {rows.length === 0 && <div className="text-xs text-fg-subtle">Нет данных</div>}
        {rows.map((r) => {
          const pct = total > 0 ? (r.revenue / total) * 100 : 0;
          return (
            <div key={r.label} className="text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-fg-muted">{r.label}</span>
                <span className="shrink-0 tabular-nums text-fg">
                  {fmtRub(r.revenue)}
                  <span className="ml-1.5 text-[10px] text-fg-subtle">{fmtNum(r.count)}</span>
                </span>
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-elevated">
                <div
                  className="h-full bg-accent/70 transition-[width] duration-500"
                  style={{ width: `${Math.max(2, pct)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Skeleton({ lines }: { lines: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton h-8" />
      ))}
    </div>
  );
}

function EmptyRow({ text }: { text: string }) {
  return <div className="py-3 text-center text-sm text-fg-subtle">{text}</div>;
}

function ErrorNote({ err }: { err: unknown }) {
  const msg = (err as ApiError)?.detail ?? "Ошибка загрузки";
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/5 p-3 text-xs text-danger">
      {msg}
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
