/**
 * Lifetime profile: spend, renewals, traffic packs, referral link.
 *
 * The bug this fixes was one line — `if (q.isError || !q.data) return null;`
 * — and it hid a production outage. When /extended-stats started
 * returning 500, the card did not report an error: it vanished. An
 * absent card on a screen full of cards reads as "this user has no
 * purchase history", which is a perfectly plausible thing to see, so
 * nobody investigated. The endpoint was broken for a long time before
 * anyone noticed.
 *
 * A failed request and an empty result are now visibly different states.
 * The component also takes its data as props rather than issuing its own
 * query, so the page controls fetching and this stays testable.
 */
import type { ReactNode } from "react";
import type { UserExtendedStats } from "@/types/user";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { KeyValueRow } from "./shared/KeyValueRow";
import { CardError, CardLoading } from "./shared/CardStates";

export interface UserProfileStatsProps {
  data: UserExtendedStats | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  /** Lets the operator retry without reloading the page. */
  onRetry?: () => void;
  className?: string;
}

/** A group of facts: a row-shaped block on tile-3 with a small title. */
export function FactGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-row bg-tile-3 px-4 py-3">
      <p className="t-mute mb-1 text-[12px] font-medium">{title}</p>
      <div className="flex flex-col gap-0.5">{children}</div>
    </div>
  );
}

export function UserProfileStats({
  data,
  isLoading = false,
  isError = false,
  onRetry,
  className,
}: UserProfileStatsProps) {
  return (
    <Surface label="Профиль" className={className}>
      {isLoading ? (
        <CardLoading label="Загружаю статистику…" rows={4} />
      ) : isError || !data ? (
        /* Said explicitly, because the previous behaviour trained
           everyone to read a missing card as "no data". */
        <CardError
          title="Статистика не загрузилась"
          note="Это ошибка запроса к /extended-stats, а не отсутствие покупок у пользователя. Данные ниже недоступны, остальные карточки верны."
          onRetry={onRetry}
        />
      ) : (
        <div className="flex flex-col gap-2">
          <FactGroup title="Деньги">
            <KeyValueRow label="Всего оплачено" value={fmtRub(data.total_spent_rubles)} />
            <KeyValueRow label="Платежей" value={fmtNum(data.total_payments_count)} />
            {data.first_paid_at && (
              <KeyValueRow label="Первая покупка" value={fmtDate(data.first_paid_at)} />
            )}
            {data.last_paid_at && (
              <KeyValueRow label="Последняя покупка" value={fmtDate(data.last_paid_at)} />
            )}
          </FactGroup>

          <FactGroup title="Подписка">
            <KeyValueRow label="Продлений" value={fmtNum(data.renewals_count)} />
            {data.reissues_count > 0 && (
              <KeyValueRow label="Перевыпусков ключа" value={fmtNum(data.reissues_count)} />
            )}
          </FactGroup>

          <FactGroup title="Обход · ГБ">
            <KeyValueRow label="ГБ куплено всего" value={fmtNum(data.traffic_gb_purchased_total)} />
            <KeyValueRow label="Покупок ГБ-паков" value={fmtNum(data.traffic_purchases_count)} />
          </FactGroup>

          <FactGroup title="Рефералы">
            <KeyValueRow
              label="Пригласил его"
              value={
                data.referrer_telegram_id
                  ? data.referrer_username
                    ? `@${data.referrer_username} · ${data.referrer_telegram_id}`
                    : String(data.referrer_telegram_id)
                  : "— (органика)"
              }
            />
            <KeyValueRow label="Сам пригласил" value={fmtNum(data.referrals_invited_count)} />
            {data.referrals_invited_count > 0 && (
              <KeyValueRow label="Из них с наградой" value={fmtNum(data.referrals_rewarded_count)} />
            )}
          </FactGroup>
        </div>
      )}
    </Surface>
  );
}
