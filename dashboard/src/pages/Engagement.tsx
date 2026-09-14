/**
 * Engagement — do our messages reach people and do they act on them:
 * renewal reminders, automated notifications, broadcasts, reach, referrals.
 * Every figure comes from /metrics/engagement (docs/dashboard/metrics.md);
 * every "?" reads lib/metricDefs.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { metricsApi, type Engagement as EngagementReport } from "@/lib/metricsApi";
import { DEF } from "@/lib/metricDefs";
import { WINDOWS } from "@/lib/periods";
import { fmtKop, fmtNum, fmtPct, fmtRelative } from "@/lib/format";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Hint } from "@/components/ui/Hint";
import { PillProgress, Segmented } from "@/components/ui/controls";
import { EmptyState, ErrorState, LoadingTiles } from "@/components/ui/states";

/** Same vocabulary as the automated-notifications screen, without emoji. */
const CATEGORY_LABEL: Record<string, string> = {
  trial: "Триал",
  subscription: "Подписка",
  welcome: "Приветствие",
  payment: "Платежи",
  referral: "Рефералы",
  gift: "Подарки",
  reminder: "Напоминания",
  other: "Прочее",
};

type Automation = EngagementReport["automations"]["by_key"][number];
type RecentBroadcast = EngagementReport["broadcasts"]["recent"][number];

function MoreLink({ to, children }: { to: string; children: string }) {
  return (
    <Link to={to} className="t-mute tap-target text-[13px] underline-offset-2 hover:underline">
      {children}
    </Link>
  );
}

function AutomationRow({ a }: { a: Automation }) {
  const rest = [
    a.failed ? `ошибок ${fmtNum(a.failed)}` : null,
    a.blocked ? `заблокировали ${fmtNum(a.blocked)}` : null,
    a.skipped ? `пропущено ${fmtNum(a.skipped)}` : null,
  ].filter(Boolean);
  return (
    <div className="rounded-row bg-tile-3 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[14px] font-medium">
            <span className="min-w-0 break-words">{a.title || a.key}</span>
            {!a.enabled && <span className="capsule-label text-[12px]">выключено</span>}
          </p>
          <p className="t-mute mt-0.5 break-words text-[12px] leading-4">
            {CATEGORY_LABEL[a.category] ?? a.category}
            {rest.length ? `. ${rest.join(", ")}` : ""}
          </p>
        </div>
        <div className="flex-none text-right">
          <div className="tabular text-[14px] font-semibold">{fmtNum(a.sent)}</div>
          <div className="t-mute text-[12px]">отправлено</div>
        </div>
      </div>
    </div>
  );
}

function BroadcastRow({ b }: { b: RecentBroadcast }) {
  return (
    <div className="rounded-row bg-tile-3 p-3">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words text-[14px] font-medium">{b.title || `Рассылка #${b.id}`}</p>
          <p className="t-mute mt-0.5 text-[12px] leading-4">
            {b.created_at ? fmtRelative(b.created_at) : "дата неизвестна"}. Доставлено {fmtNum(b.delivered)}, ошибок{" "}
            {fmtNum(b.failed)}
          </p>
        </div>
        <span className="tabular flex-none text-[14px] font-semibold">{fmtPct(b.delivery_rate)}</span>
      </div>
      <PillProgress value={b.delivery_rate} knob={false} />
    </div>
  );
}

export function Engagement() {
  const [days, setDays] = useState(30);
  const q = useQuery({
    queryKey: ["m-engagement", days],
    queryFn: () => metricsApi.engagement(days),
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const d = q.data;

  const header = (
    <PageHeader
      title="Вовлечённость"
      sub="Доходят ли сообщения бота до людей: напоминания, автоуведомления, рассылки и рефералы."
      actions={<Segmented label="Период" value={days} options={WINDOWS} onChange={setDays} />}
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

  const au = d.automations;
  const bc = d.broadcasts;
  const reach = d.reach;
  const ref = d.referrals;
  const period = `за ${days} дн.`;

  return (
    <div className={q.isFetching ? "opacity-80 transition-opacity" : "transition-opacity"}>
      {header}
      {q.isError && <ErrorState className="mb-3" error={q.error} onRetry={() => q.refetch()} />}
      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          label="Напоминания"
          value={fmtNum(d.reminders.users)}
          sub={`пользователей получили напоминание о продлении ${period}`}
          hint={DEF.reminders}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="raised"
          label="Автоуведомления отправлено"
          value={au.available ? fmtNum(au.sent) : "нет данных"}
          sub={
            au.available
              ? `Ошибок ${fmtNum(au.failed)}, заблокировали ${fmtNum(au.blocked)}, пропущено ${fmtNum(au.skipped)}`
              : "Журнала отправок ещё нет в базе."
          }
          hint={DEF.automations}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="steel"
          label="Доставка рассылок"
          value={fmtPct(bc.delivery_rate)}
          sub={
            bc.count
              ? `${fmtNum(bc.count)} рассылок. Доставлено ${fmtNum(bc.delivered)}, ошибок ${fmtNum(bc.failed)}`
              : `Рассылок ${period} не было`
          }
          hint={DEF.broadcast_delivery}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="fog"
          label="Недоступные"
          value={fmtPct(reach.unreachable_share)}
          sub={`${fmtNum(reach.unreachable)} из ${fmtNum(reach.users)} пользователей. Снимок на сейчас`}
          hint={DEF.unreachable}
        />

        <Surface
          className="sm:col-span-3 xl:col-span-6"
          variant="raised"
          label="Рефералы"
          to="/referrals"
          toLabel="Рефералы"
        >
          <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
            <div>
              <div className="tabular text-[30px] font-semibold leading-9">{fmtNum(ref.invited)}</div>
              <p className="t-mute inline-flex items-center gap-1.5 text-[13px]">
                приглашено <Hint text={DEF.referral_invited} label="Как считается: приглашено" />
              </p>
            </div>
            <span className="t-mute pb-7 text-[22px] leading-7" aria-hidden="true">
              →
            </span>
            <div>
              <div className="tabular text-[30px] font-semibold leading-9">{fmtNum(ref.converted)}</div>
              <p className="t-mute inline-flex items-center gap-1.5 text-[13px]">
                оплатили впервые <Hint text={DEF.referral_converted} label="Как считается: оплатили впервые" />
              </p>
            </div>
          </div>
          <p className="t-mute mt-3 text-[12px] leading-4">
            Оплатившие — не обязательно из приглашённых за этот же период, поэтому долю не считаем.
          </p>
        </Surface>
        <KpiTile
          className="sm:col-span-3 xl:col-span-6"
          variant="mist"
          label="Кэшбэк партнёрам"
          value={fmtKop(ref.cashback.kopecks)}
          sub={`${fmtNum(ref.cashback.count)} начислений, ${fmtNum(ref.cashback.referrers)} партнёров`}
          hint={DEF.referral_payouts}
        />

        <Surface
          className="sm:col-span-6 xl:col-span-7"
          label="Автоуведомления по типам"
          hint={DEF.automations}
          aside={<MoreLink to="/automated-notifications">Настроить</MoreLink>}
        >
          {!au.available ? (
            <EmptyState
              title="Журнала отправок ещё нет"
              hint="Таблица журнала автоуведомлений в базе не создана, поэтому отправки не посчитать. Сами уведомления при этом могут уходить."
            />
          ) : au.by_key.length === 0 ? (
            <EmptyState title={`Автоуведомлений ${period} не было`} />
          ) : (
            <ul className="flex flex-col gap-2">
              {au.by_key.map((a) => (
                <li key={a.key}>
                  <AutomationRow a={a} />
                </li>
              ))}
            </ul>
          )}
        </Surface>

        <Surface
          className="sm:col-span-6 xl:col-span-5"
          variant="raised"
          label="Последние рассылки"
          hint={DEF.broadcast_delivery}
          aside={<MoreLink to="/broadcasts">Все рассылки</MoreLink>}
        >
          {bc.recent.length === 0 ? (
            <EmptyState title={`Рассылок ${period} не было`} />
          ) : (
            <ul className="flex flex-col gap-2">
              {bc.recent.map((b) => (
                <li key={b.id}>
                  <BroadcastRow b={b} />
                </li>
              ))}
            </ul>
          )}
        </Surface>
      </Bento>
    </div>
  );
}
