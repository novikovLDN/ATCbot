import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCcw } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyAllClear,
  EmptyFailure,
  LoadingGate,
  SkeletonCard,
  SkeletonTile,
  StatCard,
} from "@/components/ui";

/**
 * «Статистика» — вторая вкладка раздела «Аналитика».
 *
 * ЧЕМ ОТЛИЧАЕТСЯ ОТ ПЕРВОЙ ВКЛАДКИ. «Метрики и доход» отвечают на вопрос
 * «сколько», эта вкладка — на вопрос «из чего»: какие продукты, какие
 * провайдеры, какие партнёры, какие рассылки. Поэтому здесь нет ни одного
 * итогового числа, которого не было бы на первой вкладке, зато каждое
 * разложено на составляющие.
 *
 * ЧТО ИСПРАВЛЕНО ПРИ ПЕРЕДЕЛКЕ (все три дефекта нашлись живьём):
 *
 * 1. ХУК ЗА УСЛОВНЫМ ВОЗВРАТОМ. В SegmentsMini стояло `if (isLoading) return`
 *    и только НИЖЕ useMemo. React считает хуки по порядку вызова: как только
 *    запрос переставал грузиться, число хуков в компоненте менялось с 1 на 2,
 *    и React падал с «rendered more hooks than during the previous render».
 *    Хуки теперь вызываются до любых возвратов — иначе дефект вернётся.
 *
 * 2. ОШИБКА, ПОКАЗАННАЯ КАК ПУСТОТА. Сегменты при отказе запроса возвращали
 *    null — блок просто исчезал со страницы, и понять, сломалось или сегментов
 *    нет, было нельзя. Рефералы вообще не проверяли isError и рисовали нули.
 *
 * 3. СВОИ ЛОКАЛЬНЫЕ Skeleton / EmptyRow / ErrorNote. Три компонента с теми же
 *    именами, что в `ui/`, но с другим поведением: скелетон без блика,
 *    «пусто» и «ошибка» одинаковым серым текстом. Взяты общие.
 */

const RANGES = [
  { hours: 24, label: "24 часа" },
  { hours: 168, label: "7 дней" },
  { hours: 720, label: "30 дней" },
] as const;

type RangeHours = (typeof RANGES)[number]["hours"];

export function Statistics() {
  const [hours, setHours] = useState<RangeHours>(168);
  const label = RANGES.find((r) => r.hours === hours)?.label ?? "период";

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
    // Приведение через unknown намеренно: как только этот эндпоинт получит
    // точный тип в lib/api.ts, прямой каст в Record<string, unknown> перестанет
    // компилироваться (индексной сигнатуры у именованного интерфейса нет).
    // Строки разбираются ниже через asNum/String, форма ответа здесь не важна.
    queryFn: () =>
      endpoints.broadcastsRecent(20) as unknown as Promise<
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

  const anyFetching =
    breakdown.isFetching ||
    referrals.isFetching ||
    topReferrers.isFetching ||
    broadcasts.isFetching;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Анализ
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Статистика
          </h1>
          <p className="mt-1 max-w-xl text-base text-fg-muted">
            Из чего складываются деньги: продукты, провайдеры, партнёры,
            рассылки. Числа обновляются сами — раз в минуту.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="pill-tabs" role="group" aria-label="Период">
            {RANGES.map((r) => (
              <button
                type="button"
                key={r.hours}
                onClick={() => setHours(r.hours)}
                aria-pressed={hours === r.hours}
                className={hours === r.hours ? "pill-tab-active" : "pill-tab"}
              >
                {r.label}
              </button>
            ))}
          </div>
          <Button
            onClick={refetchAll}
            loading={anyFetching}
            icon={<RefreshCcw className="h-3.5 w-3.5" />}
          >
            Обновить
          </Button>
        </div>
      </header>

      {/* ── Оборот за выбранный период ──────────────────────────────── */}
      <Card>
        <CardHeader title="Оборот" subtitle={`за ${label.toLowerCase()}`} />
        <CardBody>
          {breakdown.isError ? (
            <EmptyFailure
              what={`оборот за ${label.toLowerCase()}`}
              reason={
                (breakdown.error as ApiError)?.detail ??
                "Запрос не вернулся. Нули здесь читались бы как «оплат не было»."
              }
              onRetry={() => breakdown.refetch()}
            />
          ) : (
            <LoadingGate
              loading={breakdown.isLoading}
              skeleton={
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  {[0, 1, 2, 3].map((i) => (
                    <SkeletonTile key={i} />
                  ))}
                </div>
              }
              message="Считаю оборот"
            >
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Выручка"
                  value={fmtRub(breakdown.data?.total.revenue_rubles ?? 0)}
                  tone="success"
                />
                <StatCard
                  label="Оплат"
                  value={fmtNum(breakdown.data?.total.count ?? 0)}
                />
                <StatCard
                  label="Средний чек"
                  value={fmtRub(
                    breakdown.data?.total.count
                      ? breakdown.data.total.revenue_rubles /
                          breakdown.data.total.count
                      : 0,
                  )}
                  hint="выручка ÷ оплаты"
                />
                <StatCard
                  label="Продуктов продано"
                  value={fmtNum(breakdown.data?.by_type.length ?? 0)}
                  hint="разных позиций"
                />
              </div>
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Разбивки. Везде горизонтальные полосы, отсортированные по
             значению: категорий больше трёх, а пирог с четырьмя и более
             сегментами даёт иллюзию понимания (research §7.1). ──────── */}
      <Card>
        <CardHeader
          title="Из чего оборот"
          subtitle={`за ${label.toLowerCase()} · что купили, чем заплатили, по каким тарифам`}
        />
        <CardBody>
          {breakdown.isError ? (
            <EmptyFailure
              what="разбивку оборота"
              reason={
                (breakdown.error as ApiError)?.detail ??
                "Запрос не вернулся."
              }
              onRetry={() => breakdown.refetch()}
            />
          ) : (
            <LoadingGate
              loading={breakdown.isLoading}
              skeleton={
                <div className="grid gap-3 md:grid-cols-2">
                  <SkeletonCard lines={4} />
                  <SkeletonCard lines={4} />
                </div>
              }
              message="Раскладываю оборот по разрезам"
            >
              {!breakdown.data || breakdown.data.total.count === 0 ? (
                <EmptyAllClear
                  title={`За ${label.toLowerCase()} оплат не было`}
                  description="Запрос прошёл — платежей в этом окне действительно ноль. Возьмите период шире."
                />
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  <BreakdownBars
                    title="По продукту"
                    rows={breakdown.data.by_type.map((r) => ({
                      label: PT_LABEL[r.purchase_type] ?? r.purchase_type,
                      count: r.count,
                      revenue: r.revenue_rubles,
                    }))}
                  />
                  <BreakdownBars
                    title="По провайдеру"
                    rows={breakdown.data.by_provider.map((r) => ({
                      label: PROVIDER_LABEL[r.provider] ?? r.provider,
                      count: r.count,
                      revenue: r.revenue_rubles,
                    }))}
                  />
                  <BreakdownBars
                    title="Топ-15 тарифов"
                    rows={breakdown.data.by_tariff.map((r) => ({
                      label: r.tariff,
                      count: r.count,
                      revenue: r.revenue_rubles,
                    }))}
                  />
                  {breakdown.data.by_apple_nominal.length > 0 && (
                    <BreakdownBars
                      title="Apple ID — по номиналу"
                      rows={breakdown.data.by_apple_nominal.map((r) => ({
                        label: `${APPLE_REGION[r.region] ?? r.region} · ${r.nominal} ${APPLE_CUR[r.region] ?? "$"}`,
                        count: r.count,
                        revenue: r.revenue_rubles,
                      }))}
                    />
                  )}
                </div>
              )}
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Рефералы ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Рефералы"
          subtitle="за всё время · сводка и десять партнёров с наибольшей выручкой"
        />
        <CardBody className="space-y-3">
          {referrals.isError ? (
            <EmptyFailure
              what="сводку по рефералам"
              reason={
                (referrals.error as ApiError)?.detail ??
                "Запрос не вернулся. Четыре нуля выглядели бы как «партнёрская программа не работает»."
              }
              onRetry={() => referrals.refetch()}
            />
          ) : (
            <LoadingGate
              loading={referrals.isLoading}
              skeleton={
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  {[0, 1, 2, 3].map((i) => (
                    <SkeletonTile key={i} />
                  ))}
                </div>
              }
              message="Считаю рефералов"
            >
              {/* ИМЕНА ПОЛЕЙ ПРОВЕРЕНЫ ПО БЭКЕНДУ (database/referral_analytics.py,
                  get_referral_overall_stats). До правки здесь стояли
                  referred_users_count / active_referrals / referral_revenue /
                  cashback_paid — таких полей в ответе нет и не было, поэтому
                  все четыре числа ВСЕГДА показывали ноль. Сверяйте имена с
                  типом ReferralsOverall в lib/api.ts, а не с тем, как метрику
                  назвали в подписи. */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Партнёров"
                  value={fmtNum(referrals.data?.total_referrers ?? 0)}
                  hint="привели хотя бы одного"
                />
                <StatCard
                  label="Приглашённых"
                  value={fmtNum(referrals.data?.total_referrals ?? 0)}
                  hint="пришли по чужой ссылке"
                />
                <StatCard
                  label="Выручка от рефералов"
                  value={fmtRub(referrals.data?.total_revenue ?? 0)}
                  tone="success"
                />
                <StatCard
                  label="Выплачено кэшбэком"
                  value={fmtRub(referrals.data?.total_cashback_paid ?? 0)}
                  hint="ушло партнёрам"
                />
              </div>
            </LoadingGate>
          )}

          {topReferrers.isError ? (
            <EmptyFailure
              what="топ партнёров"
              reason={
                (topReferrers.error as ApiError)?.detail ?? "Запрос не вернулся."
              }
              onRetry={() => topReferrers.refetch()}
            />
          ) : (
            <LoadingGate
              loading={topReferrers.isLoading}
              skeleton={<SkeletonCard lines={5} />}
              message="Собираю топ партнёров"
            >
              {!topReferrers.data || topReferrers.data.length === 0 ? (
                <EmptyAllClear
                  title="Партнёров пока нет"
                  description="Никто ещё не привёл ни одного пользователя по своей ссылке."
                />
              ) : (
                <div className="rounded-lg border border-border">
                  <div className="border-b border-border-subtle px-3 py-2 text-2xs font-medium uppercase tracking-wider text-fg-subtle">
                    Топ-10 партнёров по выручке
                  </div>
                  <ul className="divide-y divide-border-subtle">
                    {topReferrers.data.slice(0, 10).map((r, i) => (
                      <ReferrerRow key={i} row={r} place={i + 1} />
                    ))}
                  </ul>
                </div>
              )}
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Рассылки ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Последние рассылки"
          subtitle="сколько ушло, сколько доставлено, сколько упало"
        />
        <CardBody>
          {broadcasts.isError ? (
            <EmptyFailure
              what="список рассылок"
              reason={
                (broadcasts.error as ApiError)?.detail ??
                "Запрос не вернулся. Пустой список читался бы как «рассылок не было»."
              }
              onRetry={() => broadcasts.refetch()}
            />
          ) : (
            <LoadingGate
              loading={broadcasts.isLoading}
              skeleton={<SkeletonCard lines={5} />}
              message="Загружаю рассылки"
            >
              {!broadcasts.data || broadcasts.data.length === 0 ? (
                <EmptyAllClear
                  title="Рассылок ещё не было"
                  description="Как только уйдёт первая — здесь появится её доставляемость."
                />
              ) : (
                <ul className="divide-y divide-border-subtle rounded-lg border border-border">
                  {broadcasts.data.slice(0, 20).map((b, i) => (
                    <BroadcastRow key={i} row={b} />
                  ))}
                </ul>
              )}
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Сегменты ────────────────────────────────────────────────── */}
      <SegmentsCard />
    </div>
  );
}

/**
 * Сегменты аудитории — только просмотр размеров. Управление сегментами живёт
 * в разделе «Рассылки», и дублировать его сюда не нужно.
 *
 * ВНИМАНИЕ: все хуки вызываются до первого return. Именно здесь раньше стоял
 * useMemo ниже условного выхода — см. п. 1 в шапке файла.
 */
function SegmentsCard() {
  const segments = useQuery({
    queryKey: ["statistics", "segments"],
    queryFn: endpoints.broadcastSegments,
    refetchInterval: 120_000,
  });

  const sorted = useMemo(
    () => [...(segments.data ?? [])].sort((a, b) => b.count - a.count).slice(0, 10),
    [segments.data],
  );

  return (
    <Card>
      <CardHeader
        title="Сегменты аудитории"
        subtitle="десять самых крупных · настройка и рассылка по сегменту — в разделе «Рассылки»"
      />
      <CardBody>
        {segments.isError ? (
          <EmptyFailure
            what="сегменты аудитории"
            reason={
              (segments.error as ApiError)?.detail ??
              "Запрос не вернулся. Раньше этот блок в такой ситуации просто исчезал со страницы."
            }
            onRetry={() => segments.refetch()}
          />
        ) : (
          <LoadingGate
            loading={segments.isLoading}
            skeleton={<SkeletonCard lines={4} />}
            message="Считаю сегменты"
          >
            {sorted.length === 0 ? (
              <EmptyAllClear
                title="Сегментов нет"
                description="Ни один сегмент не набрал ни одного пользователя."
              />
            ) : (
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {sorted.map((s) => (
                  <div
                    key={s.key}
                    className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-base"
                  >
                    <span className="truncate text-fg-muted">{s.label}</span>
                    <span className="shrink-0 font-semibold tabular-nums text-fg">
                      {fmtNum(s.count)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </LoadingGate>
        )}
      </CardBody>
    </Card>
  );
}

/** Строка партнёра. Числа подписаны словами, а не эмодзи: эмодзи рисуются
 *  по-разному в разных системах и не читаются экранным диктором. */
function ReferrerRow({ row, place }: { row: unknown; place: number }) {
  const r = row as Record<string, unknown>;
  const id = asNum(r.referrer_id) ?? asNum(r.telegram_id) ?? 0;
  const username = typeof r.username === "string" ? r.username : "";
  const invited = asNum(r.invited_count) ?? 0;
  const trials = asNum(r.trial_count) ?? 0;
  const paid = asNum(r.paid_count) ?? 0;
  const revenue =
    asNum(r.total_invited_revenue) ?? asNum(r.total_revenue) ?? 0;

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-base">
      <span className="w-5 shrink-0 tabular-nums text-fg-subtle">{place}</span>
      <span className="min-w-0 flex-1 truncate font-medium text-fg">
        {username ? `@${username}` : `tg:${id}`}
      </span>
      <span className="shrink-0 text-xs text-fg-muted">
        привёл <b className="tabular-nums text-fg">{fmtNum(invited)}</b>
      </span>
      <span className="shrink-0 text-xs text-fg-muted">
        триалов <b className="tabular-nums text-fg">{fmtNum(trials)}</b>
      </span>
      <span className="shrink-0 text-xs text-fg-muted">
        оплат <b className="tabular-nums text-fg">{fmtNum(paid)}</b>
      </span>
      <span className="min-w-[72px] shrink-0 text-right font-semibold tabular-nums text-fg">
        {fmtRub(revenue)}
      </span>
    </li>
  );
}

/** Строка рассылки. «Упало» показано и цветом, и словом — цвет не может быть
 *  единственным носителем смысла (research §4.11). */
function BroadcastRow({ row }: { row: Record<string, unknown> }) {
  const id = asNum(row.id) ?? 0;
  const title = String(row.title ?? "Без названия");
  const sent = asNum(row.sent_count) ?? asNum(row.sent) ?? 0;
  const failed = asNum(row.failed_count) ?? asNum(row.failed) ?? 0;
  const total = asNum(row.total_recipients) ?? sent + failed;
  const created = String(row.created_at ?? "").slice(0, 16).replace("T", " ");

  return (
    <li>
      <a
        href={`/dashboard/broadcasts?id=${id}`}
        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-base transition-colors hover:bg-bg-subtle"
      >
        <span className="w-10 shrink-0 font-mono text-xs text-fg-subtle">
          #{id}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium text-fg">
          {title}
        </span>
        <span className="hidden shrink-0 text-xs text-fg-subtle sm:inline">
          {created}
        </span>
        <span className="shrink-0 text-xs text-fg-muted">
          получателей <b className="tabular-nums text-fg">{fmtNum(total)}</b>
        </span>
        <span className="shrink-0 text-xs text-fg-muted">
          доставлено{" "}
          <b className="tabular-nums text-success">{fmtNum(sent)}</b>
        </span>
        {failed > 0 && (
          <span className="shrink-0 text-xs text-fg-muted">
            упало <b className="tabular-nums text-danger">{fmtNum(failed)}</b>
          </span>
        )}
      </a>
    </li>
  );
}

/**
 * Горизонтальные полосы, отсортированные по значению.
 *
 * Именно этот тип заменяет круговую диаграмму везде, где сегментов больше
 * трёх (research §7.2). Полоса показывает долю, число рядом — саму величину;
 * долю не приходится восстанавливать по углу сектора.
 */
function BreakdownBars({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label: string; count: number; revenue: number }>;
}) {
  const sorted = [...rows].sort((a, b) => b.revenue - a.revenue);
  const total = sorted.reduce((a, r) => a + r.revenue, 0);
  const max = Math.max(1, ...sorted.map((r) => r.revenue));

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-2 text-2xs font-medium uppercase tracking-wider text-fg-subtle">
        {title}
      </div>
      {sorted.length === 0 ? (
        <div className="py-2 text-xs text-fg-subtle">В этом разрезе пусто.</div>
      ) : (
        <div className="space-y-1.5">
          {sorted.map((r) => {
            const share = total > 0 ? (r.revenue / total) * 100 : 0;
            return (
              <div key={r.label} className="text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-fg-muted">{r.label}</span>
                  <span className="shrink-0 tabular-nums text-fg">
                    {fmtRub(r.revenue)}
                    <span className="ml-1.5 text-fg-subtle">
                      {share.toFixed(0)}% · {fmtNum(r.count)} шт
                    </span>
                  </span>
                </div>
                {/* Полоса масштабируется от максимума, а не от суммы: так
                    видно соотношение позиций между собой. Минимум 2% — иначе
                    живая, но маленькая величина выглядит как ноль. */}
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-subtle">
                  <div
                    className={cn("h-full bg-accent-9")}
                    style={{ width: `${Math.max(2, (r.revenue / max) * 100)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
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
  usa: "США",
  turkey: "Турция",
  russia: "Россия",
  india: "Индия",
};
const APPLE_CUR: Record<string, string> = {
  usa: "$",
  turkey: "TL",
  russia: "₽",
  india: "INR",
};
