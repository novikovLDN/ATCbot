import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Crown,
  RefreshCcw,
  X,
} from "lucide-react";

import { endpoints, type UserDetail } from "@/lib/api";
import { fmtDate, fmtNum, fmtRelative, fmtRub } from "@/lib/format";
import {
  Button,
  Card,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { UserAccessActions } from "./UserAccessActions";
import { UserMoneyActions } from "./UserMoneyActions";
import { UserPurchases } from "./UserPurchases";

/**
 * Панель деталей пользователя — правая половина экрана.
 *
 * ЧТО ЗДЕСЬ РЕШАЮТ: посмотреть подписку и покупки, продлить или отозвать
 * доступ, выдать скидку, поправить баланс. Всё это — в одной панели, без
 * перехода на отдельную страницу: список слева остаётся на месте, и
 * следующего человека открывают одним кликом.
 *
 * ОТКАЗ ОДНОГО БЛОКА НЕ ГАСИТ ПАНЕЛЬ. Карточка, профиль и покупки —
 * три независимых запроса. Раньше упавший «Профиль» просто исчезал
 * (return null), и человек не понимал, что блок вообще существовал;
 * теперь каждый блок либо показывает данные, либо честно говорит, что не
 * загрузился, и даёт «Повторить» на месте (ux-patterns §3.5).
 */

/** Достать строку из ответа, не веря типам: бэкенд отдаёт Record<string, unknown>. */
function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function num(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "string" && v !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function UserPanel({
  telegramId,
  onClose,
}: {
  telegramId: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["users", "detail", telegramId],
    queryFn: () => endpoints.userDetail(telegramId),
  });

  // Любое действие меняет и карточку, и историю покупок, и список слева:
  // выдача доступа переставляет строку из «истекла» в «активна».
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["users", "detail", telegramId] });
    qc.invalidateQueries({ queryKey: ["users", "extended", telegramId] });
    qc.invalidateQueries({ queryKey: ["users", "purchases", telegramId] });
    qc.invalidateQueries({ queryKey: ["users", "list"] });
  };

  if (detail.isError) {
    return (
      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-medium text-fg">tg:{telegramId}</div>
          <CloseButton onClose={onClose} />
        </div>
        <EmptyFailure
          what="карточку пользователя"
          reason="Карточка не пришла. Это отказ запроса — данные о человеке никуда не делись."
          onRetry={() => detail.refetch()}
        />
      </Card>
    );
  }

  return (
    <LoadingGate
      loading={detail.isLoading}
      skeleton={
        <Card className="space-y-3 p-4">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-24" />
          <div className="grid grid-cols-2 gap-3 pt-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        </Card>
      }
      message="Открываю карточку"
    >
      {detail.data && (
        <div className="space-y-3">
          <PanelHeader
            telegramId={telegramId}
            data={detail.data}
            onRefresh={() => detail.refetch()}
            refreshing={detail.isFetching}
            onClose={onClose}
          />
          <UserAccessActions
            telegramId={telegramId}
            data={detail.data}
            onChanged={refresh}
          />
          <UserMoneyActions
            telegramId={telegramId}
            data={detail.data}
            onChanged={refresh}
          />
          <ExtendedProfile telegramId={telegramId} />
          <UserPurchases telegramId={telegramId} />
        </div>
      )}
    </LoadingGate>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label="Закрыть карточку"
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
    >
      <X className="h-4 w-4" aria-hidden />
    </button>
  );
}

function PanelHeader({
  telegramId,
  data,
  onRefresh,
  refreshing,
  onClose,
}: {
  telegramId: number;
  data: UserDetail;
  onRefresh: () => void;
  refreshing: boolean;
  onClose: () => void;
}) {
  const user = data.user;
  const sub = data.subscription;
  const username = str(user.username);
  const expiresAt = sub ? str(sub.expires_at) : null;
  const active = Boolean(sub) && sub?.status === "active";
  const discount = data.discount ? num(data.discount.discount_percent) : null;

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-lg font-semibold text-fg">
              {username ? `@${username}` : `tg:${telegramId}`}
            </h2>
            {data.is_vip && (
              <span className="inline-flex items-center gap-1 rounded-sm bg-warning/12 px-1.5 py-0.5 text-xs font-medium text-warning">
                <Crown className="h-3 w-3" aria-hidden />
                VIP
              </span>
            )}
            <StatusBadge kind={active ? "success" : "neutral"}>
              {active ? "доступ активен" : "доступа нет"}
            </StatusBadge>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-muted">
            <span className="font-mono">tg:{telegramId}</span>
            {str(user.created_at) && <span>в базе с {fmtDate(str(user.created_at))}</span>}
            {str(user.language) && <span>язык {str(user.language)}</span>}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onRefresh}
            aria-label="Обновить карточку"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <RefreshCcw
              className={refreshing ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
              aria-hidden
            />
          </button>
          <CloseButton onClose={onClose} />
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3">
        <Fact label="Баланс" value={fmtRub(data.balance_rubles)} />
        <Fact
          label="Тариф"
          value={
            sub
              ? String(sub.tariff_display ?? sub.subscription_type ?? "—")
              : "нет подписки"
          }
        />
        <Fact
          label="Доступ до"
          value={expiresAt ? fmtDate(expiresAt) : "—"}
          hint={expiresAt ? fmtRelative(expiresAt) : undefined}
        />
        <Fact
          label="Кешбэк"
          value={`${fmtNum(data.cashback_effective_percent)}%`}
          hint={
            data.cashback_fixed_percent != null
              ? "зафиксирован админом"
              : "по уровню рефералов"
          }
        />
        <Fact
          label="Личная скидка"
          value={discount != null ? `${discount}%` : "нет"}
          hint={
            data.discount && str(data.discount.expires_at)
              ? `до ${fmtDate(str(data.discount.expires_at))}`
              : undefined
          }
        />
        <Fact
          label="Триал"
          value={data.trial ? "использован" : "не использован"}
          hint={
            data.trial && str(data.trial.trial_expires_at)
              ? `до ${fmtDate(str(data.trial.trial_expires_at))}`
              : undefined
          }
        />
      </dl>
    </Card>
  );
}

function Fact({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-border-subtle px-3 py-2">
      <dt className="text-2xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
        {label}
      </dt>
      <dd className="mt-0.5 truncate text-base font-medium text-fg">{value}</dd>
      {hint && <div className="truncate text-2xs text-fg-subtle">{hint}</div>}
    </div>
  );
}

/**
 * Финансовый и реферальный профиль. Раньше при ошибке этот блок исчезал
 * совсем — теперь говорит, что не загрузился (аудит §2).
 */
function ExtendedProfile({ telegramId }: { telegramId: number }) {
  const q = useQuery({
    queryKey: ["users", "extended", telegramId],
    queryFn: () => endpoints.userExtended(telegramId),
    staleTime: 30_000,
  });

  if (q.isError) {
    return (
      <EmptyFailure
        what="профиль: сколько заплатил и кого привёл"
        reason="Считалка не ответила. Сумма покупок неизвестна — это не значит, что покупок не было."
        onRetry={() => q.refetch()}
      />
    );
  }

  const s = q.data ?? {};
  const spent = num(s.total_spent_rubles) ?? 0;
  const payments = num(s.total_payments_count) ?? 0;
  const referrerId = num(s.referrer_telegram_id);
  const referrerName = str(s.referrer_username);
  const invited = num(s.referrals_invited_count) ?? 0;
  const rewarded = num(s.referrals_rewarded_count) ?? 0;
  const gb = num(s.traffic_gb_purchased_total) ?? 0;

  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
        Профиль
      </div>
      <LoadingGate
        loading={q.isLoading}
        skeleton={
          <div className="mt-3 space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-3.5" />
            ))}
          </div>
        }
        message="Считаю покупки и рефералов"
      >
        <dl className="mt-3 space-y-1.5 text-base">
          <Line
            label="Заплатил всего"
            value={fmtRub(spent)}
            hint="внешние оплаты, без покупок с баланса"
          />
          <Line label="Покупок" value={fmtNum(payments)} />
          <Line
            label="Первая покупка"
            value={str(s.first_paid_at) ? fmtDate(str(s.first_paid_at)) : "не было"}
          />
          <Line
            label="Последняя покупка"
            value={str(s.last_paid_at) ? fmtDate(str(s.last_paid_at)) : "не было"}
          />
          <Line label="Продлений" value={fmtNum(num(s.renewals_count) ?? 0)} />
          {gb > 0 && <Line label="Куплено ГБ обхода" value={fmtNum(gb)} />}
          <Line
            label="Пригласил его"
            value={
              referrerId
                ? referrerName
                  ? `@${referrerName} · tg:${referrerId}`
                  : `tg:${referrerId}`
                : "никто, органика"
            }
          />
          <Line
            label="Привёл сам"
            value={invited > 0 ? `${fmtNum(invited)} · с наградой ${fmtNum(rewarded)}` : "никого"}
          />
        </dl>
      </LoadingGate>
    </Card>
  );
}

function Line({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-fg-muted">
        {label}
        {hint && <span className="ml-1 text-2xs text-fg-subtle">({hint})</span>}
      </dt>
      <dd className="shrink-0 font-medium tabular-nums text-fg">{value}</dd>
    </div>
  );
}

/** Кнопка «назад к списку» для телефона, где панель занимает весь экран. */
export function BackToList({ onBack }: { onBack: () => void }) {
  return (
    <Button
      size="sm"
      onClick={onBack}
      icon={<ArrowLeft className="h-3.5 w-3.5" />}
      className="lg:hidden"
    >
      К списку
    </Button>
  );
}
