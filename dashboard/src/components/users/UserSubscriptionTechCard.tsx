/**
 * The subscription's technical state — the card that answers the three
 * questions support actually gets.
 *
 * The backend returns roughly forty subscription columns. The old screen
 * rendered two of them (tariff and expiry), which meant the panel could
 * not answer any of:
 *
 *   "Я оплатил, ключ не пришёл"  → activation_status, activation_attempts,
 *                                   last_activation_error
 *   "Почему не продлилось"       → auto_renew, last_auto_renewal_at
 *   "Дайте мне мою ссылку"       → vpn_key, remnawave_*_sub_url
 *
 * The keys in particular were being read out of the database by hand on
 * every such request, because they were not on any screen.
 *
 * A failed activation is the state most worth flagging: money has been
 * taken and nothing was delivered. v3 tiles carry no borders or rings, so
 * it is marked by the danger badge in the label row and the error block
 * directly under the tariff.
 */
import type { UserSubscription } from "@/types/user";
import {
  activationTone,
  fmtBytes,
  hasEverConnected,
  isSubscriptionActive,
  sourceLabel,
  tariffLabel,
  type ActivationTone,
} from "@/lib/userDomain";
import { fmtDate, fmtRelative } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { StatusDot } from "@/components/ui/controls";
import { KeyValueRow } from "./shared/KeyValueRow";
import { CopyButton } from "./shared/CopyButton";
import { FactGroup } from "./UserProfileStats";

export interface UserSubscriptionTechCardProps {
  subscription: UserSubscription | null;
  className?: string;
}

const ACTIVATION_LABEL: Record<ActivationTone, string> = {
  ok: "Активирована",
  pending: "Активируется",
  failed: "Ошибка активации",
  unknown: "Статус неизвестен",
};

const ACTIVATION_BADGE: Record<ActivationTone, string> = {
  ok: "badge-success",
  pending: "badge-warning",
  failed: "badge-danger",
  unknown: "badge-muted",
};

/** A long technical value: monospace, truncated, full text on hover,
    with a copy button so nobody has to select it by hand. */
function SecretRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <KeyValueRow
      label={label}
      value={value}
      mono
      title={value}
      action={<CopyButton value={value} label={label} />}
    />
  );
}

export function UserSubscriptionTechCard({ subscription, className }: UserSubscriptionTechCardProps) {
  if (!subscription) {
    return (
      <Surface label="Подписка · техданные" className={className}>
        <p className="t-mute text-[13px]">Подписки никогда не было.</p>
      </Surface>
    );
  }

  const sub = subscription;
  const tone = activationTone(sub);
  const active = isSubscriptionActive(sub);
  const connected = hasEverConnected(sub);

  // Paid for, live, and not a single byte through it. This is the
  // earliest churn signal the system has — earlier than a missed
  // renewal, because by then the refund conversation has already
  // started.
  const churnRisk = active && !connected;

  const hasKeys =
    sub.vpn_key ||
    sub.vpn_key_plus ||
    sub.remnawave_premium_sub_url ||
    sub.remnawave_bypass_sub_url ||
    sub.uuid;

  return (
    <Surface
      label="Подписка · техданные"
      className={className}
      aside={<span className={ACTIVATION_BADGE[tone]}>{ACTIVATION_LABEL[tone]}</span>}
    >
      <p className="text-[15px] font-semibold">{tariffLabel(sub.subscription_type)}</p>

      <div className="mt-3 flex flex-col gap-2">
        {/* The error string itself, not a generic "что-то пошло не так".
            It names the upstream that refused, which is the difference
            between escalating to the right team and guessing. */}
        {tone === "failed" && sub.last_activation_error && (
          <div role="alert" className="rounded-row bg-tile-3 px-4 py-3">
            <p className="flex items-center gap-2 text-[13px] font-medium text-danger">
              <StatusDot tone="err" />
              Последняя ошибка
            </p>
            <p className="t-body mt-1 break-words font-mono text-[12px] leading-5">
              {sub.last_activation_error}
            </p>
          </div>
        )}

        {churnRisk && (
          <div className="rounded-row bg-tile-3 px-4 py-3">
            <p className="flex items-center gap-2 text-[13px] font-medium text-warning">
              <StatusDot tone="warn" />
              Ни одного подключения
            </p>
            <p className="t-mute mt-0.5 text-[12px] leading-5">
              Подписка активна, но трафик не шёл ни разу — риск оттока и повод написать первым.
            </p>
          </div>
        )}

        <FactGroup title="Активация">
          <KeyValueRow
            label="Статус"
            value={sub.activation_status ?? ACTIVATION_LABEL[tone]}
            tone={tone === "failed" ? "err" : tone === "ok" ? "ok" : "warn"}
          />
          {typeof sub.activation_attempts === "number" && (
            <KeyValueRow
              label="Попыток"
              value={sub.activation_attempts}
              tone={sub.activation_attempts > 1 ? "warn" : "default"}
            />
          )}
          <KeyValueRow label="Активирована" value={fmtDate(sub.activated_at)} />
        </FactGroup>

        <FactGroup title="Срок">
          <KeyValueRow label="Истекает" value={fmtDate(sub.expires_at)} />
          {sub.expires_at && (
            <KeyValueRow
              label={active ? "Осталось" : "Истекла"}
              value={fmtRelative(sub.expires_at)}
              tone={active ? "ok" : "err"}
            />
          )}
          <KeyValueRow
            label="Автопродление"
            value={sub.auto_renew ? "Включено" : "Выключено"}
            tone={sub.auto_renew ? "ok" : "warn"}
          />
          <KeyValueRow label="Последнее автопродление" value={fmtDate(sub.last_auto_renewal_at)} />
        </FactGroup>

        <FactGroup title="Использование">
          <KeyValueRow
            label="Первый трафик"
            value={fmtDate(sub.first_traffic_at)}
            tone={connected ? "default" : "warn"}
          />
          <KeyValueRow label="Трафик" value={fmtBytes(sub.last_bytes)} />
        </FactGroup>

        {hasKeys && (
          <FactGroup title="Ключи и ссылки">
            <SecretRow label="VPN-ключ" value={sub.vpn_key} />
            <SecretRow label="VPN-ключ Plus" value={sub.vpn_key_plus} />
            <SecretRow label="Premium sub-URL" value={sub.remnawave_premium_sub_url} />
            <SecretRow label="Bypass sub-URL" value={sub.remnawave_bypass_sub_url} />
            <SecretRow label="UUID" value={sub.uuid} />
            <SecretRow label="Premium UUID" value={sub.remnawave_premium_uuid} />
          </FactGroup>
        )}

        <FactGroup title="Прочее">
          <KeyValueRow label="Источник" value={sourceLabel(sub.source)} />
          {typeof sub.admin_grant_days === "number" && sub.admin_grant_days > 0 && (
            <KeyValueRow label="Выдано админом, дней" value={sub.admin_grant_days} />
          )}
          <KeyValueRow
            label="Комбо"
            value={sub.is_combo ? "Да" : "Нет"}
            tone={sub.is_combo ? "default" : "muted"}
          />
          <KeyValueRow
            label="Только обход"
            value={sub.is_bypass_only ? "Да" : "Нет"}
            tone={sub.is_bypass_only ? "default" : "muted"}
          />
          {sub.country && <KeyValueRow label="Страна" value={sub.country.toUpperCase()} />}
        </FactGroup>
      </div>
    </Surface>
  );
}
