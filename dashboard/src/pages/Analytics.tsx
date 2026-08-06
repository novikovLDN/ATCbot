import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { ApiError, downloadCsv, endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyAllClear,
  EmptyFailure,
  LoadingGate,
  SkeletonTable,
  SkeletonTile,
  StatCard,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
} from "@/components/ui";
import { HourlyActivity } from "@/components/analytics/HourlyActivity";
import { ConversionFunnel } from "@/components/analytics/ConversionFunnel";
import { toast } from "@/store/toast";

/**
 * «Метрики и доход» — уровень 2 навигации (research §9.2).
 *
 * СЮДА ПЕРЕЕХАЛО ВСЁ, ЧТО УБРАЛИ С ГЛАВНОЙ: разбивки по тарифам, почасовая
 * активность, воронка, ARPU/LTV. Ни одно число не удалено — они ушли на
 * уровень глубже (NN/g «Reduce Clutter Without Reducing Capability»).
 *
 * ЧТОБЫ ЭТО НЕ СТАЛО СВАЛКОЙ ГРАФИКОВ, экран собран по одному правилу: каждый
 * блок отвечает на один вопрос, и вопрос написан в заголовке блока. Порядок —
 * от переменного к постоянному: сначала выбранный период, потом «за всё
 * время», потом разрезы, потом выгрузка. Переключатель периода один и стоит в
 * шапке — раньше на главной у каждого блока был свой, и сравнить два блока
 * между собой было нельзя, потому что они молча смотрели на разные окна.
 *
 * ОТКАЗ ЗАПРОСА РИСУЕТСЯ ОТКАЗОМ. До правки все три запроса на этом экране
 * при ошибке показывали нули и прочерки: «Доход 0 ₽» вместо «сервер не
 * ответил». Это главный дефект старого дашборда, и на деньгах он опаснее
 * всего. Если добавляете сюда блок — ветка isError обязана стоять ПЕРЕД
 * веткой загрузки и перед отрисовкой чисел.
 */

const RANGES = [
  { label: "24ч", hours: 24 },
  { label: "7д", hours: 168 },
  { label: "30д", hours: 720 },
  { label: "180д", hours: 4320 },
  { label: "1г", hours: 8760 },
];

const RANGE_WORDS: Record<number, string> = {
  24: "за последние сутки",
  168: "за последние 7 дней",
  720: "за последние 30 дней",
  4320: "за последние 180 дней",
  8760: "за последний год",
};

const TARIFF_LABELS: Record<string, string> = {
  basic: "Basic",
  plus: "Plus",
  basic_combo: "Basic + Combo",
  plus_combo: "Plus + Combo",
  combo_basic: "Basic + Combo",
  combo_plus: "Plus + Combo",
  proxy: "MTProxy",
  trial: "Триал",
  subscription: "Подписки",
  traffic: "Трафик ГБ",
  balance_topup: "Пополнение",
  farm: "Ферма",
};

const WINDOW_LABELS: Record<string, string> = {
  "24h": "24ч",
  "7d": "7д",
  "30d": "30д",
  "180d": "180д",
  "365d": "1г",
  "1y": "1г",
  all: "Всё время",
};

export function Analytics() {
  const [hours, setHours] = useState(720);

  const period = useQuery({
    queryKey: ["payments", "revenue", hours],
    queryFn: () => endpoints.paymentsRevenue(hours),
    refetchInterval: 20_000,
  });

  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
  });

  const breakdown = useQuery({
    queryKey: ["stats", "breakdown"],
    queryFn: endpoints.statsBreakdown,
  });

  const periodWord = RANGE_WORDS[hours] ?? "за выбранный период";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Анализ
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Метрики и доход
          </h1>
          <p className="mt-1 max-w-xl text-base text-fg-muted">
            Разрезы, которые смотрят раз в неделю, а не каждый час. Переключатель
            справа задаёт период для первого блока; остальные блоки называют
            своё окно сами.
          </p>
        </div>

        {/* Один переключатель на весь верх экрана. Подпись у блока ниже
            повторяет выбранное окно словами — иначе через минуту не помнишь,
            на что смотришь. */}
        <div className="pill-tabs" role="group" aria-label="Период">
          {RANGES.map((r) => (
            <button
              key={r.hours}
              type="button"
              onClick={() => setHours(r.hours)}
              aria-pressed={hours === r.hours}
              className={hours === r.hours ? "pill-tab-active" : "pill-tab"}
            >
              {r.label}
            </button>
          ))}
        </div>
      </header>

      {/* ── Блок 1: сколько денег принёс выбранный период ───────────── */}
      <Card>
        <CardHeader title="Сколько заработали" subtitle={periodWord} />
        <CardBody>
          {period.isError ? (
            <EmptyFailure
              what={`выручку ${periodWord}`}
              reason={
                (period.error as ApiError)?.detail ??
                "Запрос не вернулся. Нули здесь читались бы как «продаж не было»."
              }
              onRetry={() => period.refetch()}
            />
          ) : (
            <LoadingGate
              loading={period.isLoading}
              skeleton={
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  {[0, 1, 2, 3].map((i) => (
                    <SkeletonTile key={i} />
                  ))}
                </div>
              }
              message="Считаю выручку за период"
            >
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Доход"
                  value={fmtRub(period.data?.revenue_rubles)}
                  tone="success"
                />
                <StatCard
                  label="Платежей"
                  value={fmtNum(period.data?.payments_count)}
                  hint="успешных операций"
                />
                <StatCard
                  label="Средний чек"
                  value={fmtRub(period.data?.avg_check_rubles)}
                  hint="доход ÷ платежи"
                />
                <StatCard
                  label="Типов покупок"
                  value={fmtNum(Object.keys(period.data?.by_type ?? {}).length)}
                  hint="сколько разных продуктов купили"
                />
              </div>
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Блок 2: показатели на одного человека, за всё время ─────── */}
      <Card>
        <CardHeader
          title="Сколько приносит один человек"
          subtitle="за всё время · переключатель периода на эти четыре числа не влияет"
        />
        <CardBody>
          {revenue.isError ? (
            <EmptyFailure
              what="показатели на пользователя"
              reason={
                (revenue.error as ApiError)?.detail ??
                "Запрос не вернулся. ARPU 0 ₽ означал бы, что платящих нет вовсе."
              }
              onRetry={() => revenue.refetch()}
            />
          ) : (
            <LoadingGate
              loading={revenue.isLoading}
              skeleton={
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  {[0, 1, 2, 3].map((i) => (
                    <SkeletonTile key={i} />
                  ))}
                </div>
              }
              message="Считаю ARPU и LTV"
            >
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Доход всего"
                  value={fmtRub(revenue.data?.total_revenue_rubles)}
                  tone="accent"
                  hint="с первого дня работы"
                />
                <StatCard
                  label="Платящих юзеров"
                  value={fmtNum(revenue.data?.paying_users)}
                  hint="хоть раз заплатили"
                />
                {/* ARPU и LTV — две РАЗНЫЕ метрики, и подпись обязана называть
                    знаменатель. Они считались одинаково (выручка / платящие) и
                    всегда показывали одно число под двумя названиями; на сводке
                    жили одной склеенной карточкой «ARPU / средний LTV», где
                    различить их было нельзя вовсе. Уберёте hint — дефект
                    вернётся в форме «два числа, непонятно чьих». */}
                <StatCard
                  label="ARPU"
                  hint="на всю базу"
                  value={fmtRub(revenue.data?.arpu_rubles)}
                />
                <StatCard
                  label="LTV"
                  hint="на платящего"
                  value={fmtRub(revenue.data?.avg_ltv_rubles)}
                />
              </div>
            </LoadingGate>
          )}
        </CardBody>
      </Card>

      {/* ── Блок 3: что именно покупают ─────────────────────────────── */}
      <BreakdownTable
        data={breakdown.data}
        loading={breakdown.isLoading}
        error={breakdown.isError ? breakdown.error : null}
        onRetry={() => breakdown.refetch()}
      />

      {/* ── Блок 4 и 5: переехали со сводки. На главной из них не следует
             действия в ближайший час, а место они занимали наравне с
             деньгами (research §9.3). ──────────────────────────────── */}
      <HourlyActivity />
      <ConversionFunnel />

      <ExportSection />
    </div>
  );
}

interface BreakdownPayload {
  [category: string]: {
    [window: string]: { count?: number; revenue?: number } | undefined;
  };
}

/**
 * Покупки по тарифам: строки — продукты, колонки — окна времени.
 *
 * Почему таблица, а не круговая диаграмма: категорий здесь больше трёх (у нас
 * их 8–12), а в пироге больше трёх сегментов дают иллюзию понимания при
 * искажении данных (research §7.1, Smashing). К тому же тут два числа на
 * ячейку — выручка и количество, — и пирог их показать не может в принципе.
 */
function BreakdownTable({
  data,
  loading,
  error,
  onRetry,
}: {
  data: unknown;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  // Приводим форму бэкенда (словарь тарифов из словарей окон) к двумерной
  // таблице. Имени поля для выручки не доверяем: у разных вызывающих там
  // копейки или рубли — определяем по порядку величины.
  const { categories, windows, rows } = useMemo(() => {
    const empty = {
      categories: [] as string[],
      windows: [] as string[],
      rows: [] as Array<{
        category: string;
        cells: Array<{ window: string; count: number; revenue: number }>;
      }>,
    };
    if (!data || typeof data !== "object") return empty;
    const payload = data as BreakdownPayload;
    const cats = Object.keys(payload).filter(
      (c) => payload[c] && typeof payload[c] === "object",
    );
    const winsSet = new Set<string>();
    for (const c of cats) {
      const inner = payload[c] || {};
      Object.keys(inner).forEach((w) => winsSet.add(w));
    }
    const winOrder = ["24h", "7d", "30d", "180d", "365d", "1y", "all"];
    const wins = Array.from(winsSet).sort(
      (a, b) => winOrder.indexOf(a) - winOrder.indexOf(b),
    );
    const rows = cats.map((cat) => {
      const inner = payload[cat] || {};
      return {
        category: cat,
        cells: wins.map((w) => {
          const v = inner[w] ?? {};
          const rawRev = typeof v.revenue === "number" ? v.revenue : 0;
          const revenue = rawRev >= 100_000 ? rawRev / 100 : rawRev;
          return {
            window: w,
            count: typeof v.count === "number" ? v.count : 0,
            revenue,
          };
        }),
      };
    });
    return { categories: cats, windows: wins, rows };
  }, [data]);

  const totalsPerWindow = useMemo(() => {
    const t = new Map<string, { count: number; revenue: number }>();
    for (const w of windows) t.set(w, { count: 0, revenue: 0 });
    for (const row of rows) {
      for (const c of row.cells) {
        const cur = t.get(c.window) ?? { count: 0, revenue: 0 };
        cur.count += c.count;
        cur.revenue += c.revenue;
        t.set(c.window, cur);
      }
    }
    return t;
  }, [rows, windows]);

  return (
    <Card>
      <CardHeader
        title="Что покупают"
        subtitle="строки — продукты, колонки — окна времени · выручка сверху, число покупок снизу"
      />
      <CardBody>
        {error ? (
          <EmptyFailure
            what="разбивку по тарифам"
            reason={
              (error as ApiError)?.detail ??
              "Запрос не вернулся. Пустая таблица здесь означала бы, что не покупают ничего."
            }
            onRetry={onRetry}
          />
        ) : (
          <LoadingGate
            loading={loading}
            skeleton={<SkeletonTable rows={5} cols={5} />}
            message="Считаю покупки по тарифам"
          >
            {categories.length === 0 || windows.length === 0 ? (
              // Не «Нет данных» серым текстом: разбивка пуста тогда, когда
              // покупок не было ни в одном окне, а это отдельный факт.
              <EmptyAllClear
                title="Покупок ещё не было"
                description="Ни в одном окне времени не зафиксировано ни одной оплаты. Как только пройдёт первая — она появится здесь."
              />
            ) : (
              <TableScroll>
                <Table density="compact" className="min-w-[600px]">
                  <THead>
                    <tr>
                      <TH>Продукт</TH>
                      {windows.map((w) => (
                        <TH key={w} numeric>
                          {WINDOW_LABELS[w] ?? w}
                        </TH>
                      ))}
                    </tr>
                  </THead>
                  <TBody>
                    {rows.map((row) => (
                      <TR key={row.category}>
                        <TD>{TARIFF_LABELS[row.category] ?? row.category}</TD>
                        {row.cells.map((c) => (
                          <TD key={c.window} numeric className="align-top">
                            <div className="font-medium text-fg">
                              {fmtRub(c.revenue)}
                            </div>
                            <div className="text-2xs text-fg-subtle">
                              {fmtNum(c.count)} шт
                            </div>
                          </TD>
                        ))}
                      </TR>
                    ))}
                    <TR className="bg-bg-subtle font-medium">
                      <TD>Итого</TD>
                      {windows.map((w) => {
                        const t = totalsPerWindow.get(w) ?? {
                          count: 0,
                          revenue: 0,
                        };
                        return (
                          <TD key={w} numeric>
                            <div className="text-fg">{fmtRub(t.revenue)}</div>
                            <div className="text-2xs text-fg-muted">
                              {fmtNum(t.count)} шт
                            </div>
                          </TD>
                        );
                      })}
                    </TR>
                  </TBody>
                </Table>
              </TableScroll>
            )}
          </LoadingGate>
        )}
      </CardBody>
    </Card>
  );
}

function ExportSection() {
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (path: string, filename: string, label: string) => {
    setBusy(label);
    try {
      await downloadCsv(path, filename);
      toast.success(`Скачан ${filename}`);
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось скачать");
    } finally {
      setBusy(null);
    }
  };

  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");

  return (
    <Card>
      <CardHeader
        title="Выгрузка"
        subtitle="CSV стримится прямо из базы авторизованным запросом — токен в адрес не попадает"
      />
      <CardBody>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Button
            onClick={() =>
              download("/export/users.csv", `users_${stamp}.csv`, "users")
            }
            loading={busy === "users"}
            disabled={busy !== null}
            icon={<Download className="h-3.5 w-3.5" />}
            className="justify-start"
          >
            Все пользователи (users.csv)
          </Button>
          <Button
            onClick={() =>
              download(
                "/export/subscriptions.csv",
                `subscriptions_${stamp}.csv`,
                "subscriptions",
              )
            }
            loading={busy === "subscriptions"}
            disabled={busy !== null}
            icon={<Download className="h-3.5 w-3.5" />}
            className="justify-start"
          >
            Активные подписки (subscriptions.csv)
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
