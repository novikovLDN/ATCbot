import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, ExternalLink, X } from "lucide-react";

import { endpoints, type PurchaseDetail } from "@/lib/api";
import { fmtDate, fmtRelative, fmtRub } from "@/lib/format";
import {
  Card,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { isExternalMoney, providerLabel, purchaseLabel, statusMeta } from "./labels";
import { stageMeta } from "./stages";

/**
 * Разбор одного платежа.
 *
 * ГЛАВНЫЙ ВОПРОС ЭКРАНА — «деньги ушли, товар выдан?». Ответ собирается
 * из двух вещей сразу: состояния покупки и состояния подписки на момент
 * просмотра. Поэтому здесь есть блок «Что с этим платежом», а не только
 * перечисление полей: перечисление полей заставляет админа делать вывод
 * самому, и делает он его по-разному.
 *
 * Данные берутся из pending_purchases — той же таблицы, из которой
 * пришла строка ленты. Соседний эндпоинт /payments/{id} читает
 * устаревшую таблицу payments с другой нумерацией: подставите туда id из
 * ленты — откроете чужую запись.
 */
export function PaymentPanel({
  purchaseId,
  onClose,
}: {
  purchaseId: number;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["payments", "purchase", purchaseId],
    queryFn: () => endpoints.purchaseDetail(purchaseId),
    staleTime: 15_000,
  });

  if (detail.isError) {
    return (
      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-medium text-fg">Платёж №{purchaseId}</div>
          <CloseButton onClose={onClose} />
        </div>
        <EmptyFailure
          what="разбор платежа"
          reason="Карточка платежа не пришла. Это отказ запроса — сам платёж никуда не делся."
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
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="h-20" />
        </Card>
      }
      message="Открываю платёж"
    >
      {detail.data && <PanelBody data={detail.data} onClose={onClose} />}
    </LoadingGate>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label="Закрыть разбор платежа"
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
    >
      <X className="h-4 w-4" aria-hidden />
    </button>
  );
}

/** Итог по платежу: ушли ли деньги и есть ли у человека доступ. */
function verdict(data: PurchaseDetail): {
  tone: "ok" | "warn" | "bad";
  title: string;
  detail: string;
} {
  const status = data.purchase.status;
  const sub = data.subscription;
  const accessLive =
    sub?.status === "active" &&
    sub.expires_at !== null &&
    new Date(sub.expires_at).getTime() > Date.now();
  const isSubscription = (data.purchase.purchase_type ?? "subscription") === "subscription";

  if (status === "paid") {
    if (!isSubscription) {
      return {
        tone: "ok",
        title: "Оплачено",
        detail:
          "Покупка засчитана. Это не подписка, поэтому срок доступа с ней не связан — проверяйте товар в его собственном разделе.",
      };
    }
    if (accessLive) {
      return {
        tone: "ok",
        title: "Оплачено, доступ выдан",
        detail: `Подписка действует до ${fmtDate(sub?.expires_at ?? null)}. Делать ничего не нужно.`,
      };
    }
    return {
      tone: "bad",
      title: "Деньги приняты, доступа нет",
      detail:
        "Покупка оплачена, но действующей подписки не видно. Выдайте доступ вручную из карточки — оплату повторять не нужно.",
    };
  }

  if (status === "pending") {
    return {
      tone: "warn",
      title: "Счёт выставлен, оплата не подтверждена",
      detail:
        "Подтверждения от провайдера не было. Если человек говорит, что заплатил, — ищите платёж в кабинете провайдера по сумме и времени, затем выдайте доступ вручную.",
    };
  }

  if (status === "expired") {
    return {
      tone: "warn",
      title: "Счёт истёк",
      detail:
        "Оплату не довели до конца, счёт закрылся по времени. Денег по нему не приходило.",
    };
  }

  return {
    tone: "warn",
    title: `Состояние: ${status ?? "неизвестно"}`,
    detail: "Такого состояния экран не знает — смотрите ошибки ниже и журнал провайдера.",
  };
}

function PanelBody({ data, onClose }: { data: PurchaseDetail; onClose: () => void }) {
  const p = data.purchase;
  const state = statusMeta(p.status);
  const v = verdict(data);
  const internal = !isExternalMoney(p.payment_provider);

  return (
    <div className="space-y-3">
      <Card className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-2xl font-semibold text-fg">
                {fmtRub(p.price_rubles)}
              </span>
              <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
            </div>
            <div className="mt-1 text-base text-fg">{purchaseLabel(p)}</div>
            <div className="mt-0.5 text-xs text-fg-muted">
              {fmtDate(p.created_at)} · {fmtRelative(p.created_at)}
            </div>
          </div>
          <CloseButton onClose={onClose} />
        </div>

        {internal && (
          <p className="mt-3 rounded-md bg-bg-subtle px-3 py-2 text-xs text-fg-muted">
            Оплачено с внутреннего баланса. В выручку эта сумма не входит —
            деньги были посчитаны, когда баланс пополняли.
          </p>
        )}

        <div
          className={
            v.tone === "bad"
              ? "mt-3 flex items-start gap-2 rounded-md border border-danger/40 bg-danger/[0.06] px-3 py-2.5"
              : v.tone === "warn"
                ? "mt-3 flex items-start gap-2 rounded-md border border-warning/40 bg-warning/[0.06] px-3 py-2.5"
                : "mt-3 flex items-start gap-2 rounded-md border border-success/40 bg-success/[0.06] px-3 py-2.5"
          }
        >
          {v.tone === "ok" ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
          ) : (
            <AlertTriangle
              className={
                v.tone === "bad"
                  ? "mt-0.5 h-4 w-4 shrink-0 text-danger"
                  : "mt-0.5 h-4 w-4 shrink-0 text-warning"
              }
              aria-hidden
            />
          )}
          <div className="min-w-0">
            <div className="text-base font-medium text-fg">{v.title}</div>
            <div className="mt-0.5 text-base text-fg-muted">{v.detail}</div>
          </div>
        </div>

        <dl className="mt-3 space-y-1.5 text-base">
          <Row label="Способ оплаты" value={providerLabel(p.payment_provider)} />
          {p.period_days != null && <Row label="Период" value={`${p.period_days} дн`} />}
          {p.country && <Row label="Страна пакета" value={p.country.toUpperCase()} />}
          {p.promo_code && <Row label="Промокод" value={p.promo_code} mono />}
          {p.purchase_id && <Row label="Номер покупки" value={p.purchase_id} mono />}
          {p.provider_invoice_id && (
            <Row label="Счёт у провайдера" value={p.provider_invoice_id} mono />
          )}
        </dl>
      </Card>

      <Card className="p-4">
        <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
          Кто платил
        </div>
        <div className="mt-2 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-base font-medium text-fg">
              {data.user.username ? `@${data.user.username}` : `tg:${data.user.telegram_id ?? "—"}`}
            </div>
            <div className="mt-0.5 text-xs text-fg-muted">
              баланс {fmtRub(data.user.balance_rubles)}
              {data.subscription
                ? ` · подписка ${data.subscription.subscription_type ?? "—"}${
                    data.subscription.expires_at
                      ? ` до ${fmtDate(data.subscription.expires_at)}`
                      : ""
                  }`
                : " · подписки нет"}
            </div>
          </div>
          {data.user.telegram_id && (
            <Link
              to={`/users?tg=${data.user.telegram_id}`}
              className="inline-flex shrink-0 items-center gap-1 text-xs text-accent-text underline-offset-2 hover:underline"
            >
              Открыть карточку
              <ExternalLink className="h-3 w-3" aria-hidden />
            </Link>
          )}
        </div>
      </Card>

      {data.errors.length > 0 && (
        <Card className="p-4">
          <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
            Отказы по этой покупке · {data.errors.length}
          </div>
          <ul className="mt-2 space-y-2.5">
            {data.errors.map((e) => {
              const meta = stageMeta(e.stage);
              return (
                <li key={e.id} className="rounded-md border border-border-subtle p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge kind="failure">{meta.label}</StatusBadge>
                    <span className="text-xs text-fg-muted">
                      шаг {meta.step} · {fmtDate(e.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 text-base text-fg-muted">{meta.what}</div>
                  <div className="mt-1 text-base text-fg">{meta.next}</div>
                  {e.error_message && (
                    <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded-sm bg-bg-subtle p-2 font-mono text-2xs text-fg-muted">
                      {e.error_message}
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-fg-muted">{label}</dt>
      <dd
        className={
          mono
            ? "min-w-0 truncate font-mono text-xs text-fg"
            : "min-w-0 truncate font-medium text-fg"
        }
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}
