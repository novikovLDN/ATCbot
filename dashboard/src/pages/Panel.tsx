/**
 * Panel — Remnawave, read only: users by status, online, traffic, nodes,
 * devices, and how the panel compares with the database.
 * Source: /panel/* (app/services/panel_stats.py, cached 45 s).
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { metricsApi, type PanelNode } from "@/lib/metricsApi";
import { fmtAxisBytes, fmtBytes, fmtDay, fmtNum } from "@/lib/format";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { ListRow, Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { ShareList, TrendChart } from "@/components/ui/charts";
import { EmptyState, ErrorState, LoadingTiles, Skeleton } from "@/components/ui/states";

const STATUS: { key: "ACTIVE" | "LIMITED" | "EXPIRED" | "DISABLED"; label: string; tone: Tone; hint: string }[] = [
  { key: "ACTIVE", label: "Активны", tone: "ok", hint: "Доступ открыт" },
  { key: "LIMITED", label: "Исчерпан лимит", tone: "warn", hint: "Кончились гигабайты обхода" },
  { key: "EXPIRED", label: "Истекли", tone: "idle", hint: "Срок закончился" },
  { key: "DISABLED", label: "Отключены", tone: "err", hint: "Выключены вручную или ботом" },
];
const NODE_TONE: Record<PanelNode["state"], Tone> = { online: "ok", connecting: "warn", offline: "err", disabled: "idle" };
const NODE_LABEL: Record<PanelNode["state"], string> = {
  online: "в сети",
  connecting: "подключается",
  offline: "не в сети",
  disabled: "выключена",
};
const RANGES = [
  { value: 7, label: "7 дней" },
  { value: 14, label: "14 дней" },
  { value: 30, label: "30 дней" },
];

function PanelDown() {
  return (
    <Surface variant="steel" label="Панель недоступна">
      <p className="flex items-center gap-2 text-[14px]">
        <StatusDot tone="err" /> Remnawave не ответила за 6 секунд. Данные бота на других экранах от этого не зависят.
      </p>
    </Surface>
  );
}

export function Panel() {
  const [range, setRange] = useState(14);
  const ov = useQuery({ queryKey: ["panel-overview"], queryFn: metricsApi.panelOverview, refetchInterval: 60_000 });
  const nodes = useQuery({ queryKey: ["panel-nodes"], queryFn: metricsApi.panelNodes, refetchInterval: 60_000 });
  const bw = useQuery({
    queryKey: ["panel-bandwidth", range],
    queryFn: () => metricsApi.panelBandwidth(range),
    placeholderData: (prev) => prev,
  });

  const chart = useMemo(() => {
    const b = bw.data;
    if (!b?.available) return { data: [] as Record<string, number | string>[], series: [] };
    const top = b.series.slice(0, 4);
    const data = b.categories.map((date, i) => {
      const row: Record<string, number | string> = { date, total: b.total?.[i] ?? 0 };
      top.forEach((s, k) => (row[`n${k}`] = s.data[i] ?? 0));
      return row;
    });
    return {
      data,
      series: [
        { key: "total", label: "Все ноды", highlight: true },
        ...top.map((s, k) => ({ key: `n${k}`, label: s.name })),
      ],
    };
  }, [bw.data]);

  const header = (
    <PageHeader
      title="Панель"
      sub={`Remnawave, только чтение. Данные обновляются не чаще раза в ${ov.data?.cache_ttl_seconds ?? 45} секунд.`}
    />
  );
  if (!ov.data) {
    return (
      <>
        {header}
        {ov.isError ? <ErrorState error={ov.error} onRetry={() => ov.refetch()} /> : <LoadingTiles count={6} />}
      </>
    );
  }

  const { system, bandwidth, hwid, discrepancy } = ov.data;
  const counts = system.status_counts;

  return (
    <>
      {header}
      <Bento>
        {!system.available ? (
          <div className="sm:col-span-6 xl:col-span-12">
            <PanelDown />
          </div>
        ) : (
          <>
            <KpiTile
              className="sm:col-span-6 xl:col-span-4"
              size="hero"
              label="Онлайн сейчас"
              value={fmtNum(system.online_now)}
              sub={`За сутки ${fmtNum(system.online_day)}, за неделю ${fmtNum(system.online_week)}. Ни разу не подключались ${fmtNum(system.never_online)}.`}
            />
            <Surface
              className="sm:col-span-6 xl:col-span-8"
              variant="raised"
              label="Пользователи панели"
              aside={<span className="t-mute text-[12px]">всего {fmtNum(system.users_total)}</span>}
            >
              <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {STATUS.map((s) => (
                  <li key={s.key}>
                    <ListRow leading={<StatusDot tone={s.tone} />} title={s.label} meta={s.hint} value={fmtNum(counts?.[s.key])} />
                  </li>
                ))}
              </ul>
              <p className="t-mute mt-3 text-[12px] leading-4">
                У каждого пользователя бота в панели две записи: основная подписка и обход.
              </p>
            </Surface>
          </>
        )}

        <Surface className="sm:col-span-6 xl:col-span-5" variant="steel" label="Панель и база">
          {!discrepancy.available ? (
            <p className="text-[14px]">Сравнение появится, когда панель ответит.</p>
          ) : (
            <>
              <div className="tabular text-[30px] font-semibold leading-9">
                {(discrepancy.difference ?? 0) > 0 ? "+" : ""}
                {fmtNum(discrepancy.difference)}
              </div>
              <p className="t-mute mt-1 text-[13px] leading-5">
                Разница между активными записями в панели и тем, что ожидает база.
              </p>
              <div className="mt-4 flex flex-col gap-2">
                <ListRow title="В панели активны или с лимитом" value={fmtNum(discrepancy.panel_active_or_limited)} />
                <ListRow
                  title="Ожидает база"
                  meta={`${fmtNum(discrepancy.db_premium_active)} подписок + ${fmtNum(discrepancy.db_bypass_entities)} записей обхода`}
                  value={fmtNum(discrepancy.db_expected)}
                />
              </div>
              <p className="t-mute mt-3 text-[12px] leading-4">
                Большая разница — повод открыть аудит трафика и обхода. Запись обхода с исчерпанным лимитом — норма.
              </p>
            </>
          )}
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-7" label="Трафик">
          {!bandwidth.available ? (
            <EmptyState title="Нет данных о трафике" hint="Панель не ответила на запрос статистики трафика." />
          ) : (
            <ul className="flex flex-col gap-2">
              {(
                [
                  ["day", "Сегодня", "вчера"],
                  ["week", "7 дней", "прошлые 7"],
                  ["days30", "30 дней", "прошлые 30"],
                  ["month", "Этот месяц", "прошлый месяц"],
                  ["year", "Этот год", ""],
                ] as const
              ).map(([k, label, prevLabel]) => {
                const st = bandwidth[k];
                // Bytes through fmtBytes so every row reads "2,1 ТБ" like
                // "За всё время"; the panel's own string ("2.1 TiB") is the fallback.
                const cur = st?.current_bytes != null ? fmtBytes(st.current_bytes) : st?.current;
                const prev = st?.previous_bytes ? fmtBytes(st.previous_bytes) : st?.previous;
                return (
                  <li key={k}>
                    <ListRow
                      title={label}
                      meta={prevLabel && prev ? `${prevLabel}: ${prev}` : undefined}
                      value={cur ?? "—"}
                    />
                  </li>
                );
              })}
              <li>
                <ListRow title="За всё время" value={fmtBytes(system.traffic_lifetime_bytes)} />
              </li>
            </ul>
          )}
        </Surface>

        <Surface
          className="sm:col-span-6 xl:col-span-7"
          label="Ноды"
          aside={
            nodes.data?.available && (
              <span className="t-mute text-[12px]">
                в сети {fmtNum(nodes.data.online)} из {fmtNum(nodes.data.total)}
              </span>
            )
          }
        >
          {nodes.isError ? (
            <ErrorState error={nodes.error} onRetry={() => nodes.refetch()} />
          ) : !nodes.data ? (
            <Skeleton className="h-40 w-full" />
          ) : !nodes.data.available ? (
            <EmptyState title="Список нод недоступен" hint="Панель не ответила. Попробуйте позже." />
          ) : (
            <ul className="flex flex-col gap-2">
              {nodes.data.nodes.map((n) => (
                <li key={n.uuid}>
                  <ListRow
                    leading={<StatusDot tone={NODE_TONE[n.state]} label={NODE_LABEL[n.state]} />}
                    title={`${n.name}${n.country ? `, ${n.country}` : ""}`}
                    meta={
                      n.state === "offline" && n.status_message
                        ? n.status_message
                        : `${NODE_LABEL[n.state]}${n.provider ? `, ${n.provider}` : ""}. Трафик ${fmtBytes(n.traffic_used_bytes)}${n.traffic_limit_bytes ? ` из ${fmtBytes(n.traffic_limit_bytes)}` : ""}`
                    }
                    value={`${fmtNum(n.users_online)} онлайн`}
                  />
                </li>
              ))}
            </ul>
          )}
        </Surface>

        <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Устройства">
          {!hwid.available ? (
            <EmptyState title="Нет данных об устройствах" />
          ) : (
            <>
              <div className="tabular text-[30px] font-semibold leading-9">{fmtNum(hwid.unique_devices)}</div>
              <p className="t-mute mb-4 mt-1 text-[13px]">
                уникальных устройств, в среднем {hwid.avg_per_user?.toFixed(1).replace(".", ",")} на пользователя
              </p>
              <ShareList
                rows={(hwid.by_platform ?? []).map((p, i) => ({ key: p.platform, label: p.platform, value: p.count, highlight: i === 0 }))}
                format={fmtNum}
              />
            </>
          )}
        </Surface>

        <Surface
          className="sm:col-span-6 xl:col-span-12"
          label="Трафик по нодам"
          aside={<Segmented label="Период графика" value={range} options={RANGES} onChange={setRange} />}
        >
          {bw.isError ? (
            <ErrorState error={bw.error} onRetry={() => bw.refetch()} />
          ) : !bw.data ? (
            <Skeleton className="h-64 w-full" />
          ) : !bw.data.available || chart.data.length === 0 ? (
            <EmptyState title="Нет данных за период" />
          ) : (
            <TrendChart
              data={chart.data}
              x="date"
              series={chart.series}
              format={(v) => fmtBytes(v)}
              yFormat={fmtAxisBytes}
              xFormat={fmtDay}
              height={260}
            />
          )}
        </Surface>
      </Bento>
    </>
  );
}
