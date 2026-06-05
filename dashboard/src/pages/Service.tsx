import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Power,
  RefreshCcw,
  Clock,
  Save,
  Wallet,
  Zap,
  ShieldOff,
  PlayCircle,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

export function Service() {
  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Операции
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Сервис
        </h1>
      </header>

      <IncidentSection />
      <PendingActivationsSection />
      <PendingPaymentsSection />
    </div>
  );
}

function IncidentSection() {
  const qc = useQueryClient();
  const incident = useQuery({
    queryKey: ["incident"],
    queryFn: endpoints.incidentGet,
    refetchInterval: 60_000,
  });

  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (incident.data && !dirty) {
      setText(incident.data.incident_text ?? "");
    }
  }, [incident.data, dirty]);

  const save = useMutation({
    mutationFn: (body: { is_active: boolean; incident_text?: string | null }) =>
      endpoints.incidentSet(body),
    onSuccess: (data) => {
      toast.success(
        data.is_active ? "Инцидент-режим включён" : "Инцидент-режим выключен",
      );
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["incident"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сохранить"),
  });

  const isActive = incident.data?.is_active ?? false;

  return (
    <section
      className={
        isActive
          ? "card border-warning/40 bg-warning/10 p-5"
          : "card p-5"
      }
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div
            className={
              isActive
                ? "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-warning/15 text-warning"
                : "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border"
            }
          >
            <AlertTriangle className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Режим инцидента
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Баннер всем пользователям
            </h2>
            <p className="mt-1 text-sm text-fg-muted">
              Текст появится у каждого юзера на главном экране бота. Используй
              для предупреждений о тех. работах, перебоях оплаты, и т.п.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() =>
            save.mutate({
              is_active: !isActive,
              incident_text: text || null,
            })
          }
          disabled={save.isPending}
          className={isActive ? "btn-danger" : "btn-primary"}
        >
          {save.isPending ? <Spinner /> : <Power className="h-3.5 w-3.5" />}
          {isActive ? "Выключить" : "Включить"}
        </button>
      </div>

      <label className="block">
        <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Текст (HTML)
        </div>
        <textarea
          className="input min-h-[120px] resize-y leading-relaxed"
          value={text}
          maxLength={2000}
          onChange={(e) => {
            setText(e.target.value);
            setDirty(true);
          }}
          placeholder="Например: ⚠️ Сейчас наблюдаются перебои с оплатой через СБП. Используйте карту."
        />
      </label>

      {dirty && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => {
              setText(incident.data?.incident_text ?? "");
              setDirty(false);
            }}
            className="btn-ghost"
            disabled={save.isPending}
          >
            Сбросить
          </button>
          <button
            type="button"
            onClick={() => save.mutate({ is_active: isActive, incident_text: text })}
            disabled={save.isPending}
            className="btn-primary"
          >
            {save.isPending ? <Spinner /> : <Save className="h-3.5 w-3.5" />}
            Сохранить текст
          </button>
        </div>
      )}
    </section>
  );
}

function PendingActivationsSection() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["activations", "pending"],
    queryFn: () => endpoints.activationsPending(200),
    refetchInterval: 15_000,
  });

  const retry = useMutation({
    mutationFn: (subscriptionId: number) =>
      endpoints.activationRetry(subscriptionId),
    onSuccess: (data) => {
      if (data.ok) {
        toast.success(`Подписка #${data.subscription_id} активирована`);
      } else {
        toast.error(
          data.error_message ??
            `Ретрай #${data.subscription_id} не удался — оставлена в очереди`,
        );
      }
      qc.invalidateQueries({ queryKey: ["activations"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось дёрнуть retry"),
  });

  const total = list.data?.total ?? 0;
  const rows = list.data?.rows ?? [];

  return (
    <section className="card p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div
            className={
              total > 0
                ? "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-warning/15 text-warning"
                : "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border"
            }
          >
            <Zap className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Pending активации VPN
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Очередь провизии
              {total > 0 && (
                <span className="ml-2 badge-warning">{fmtNum(total)}</span>
              )}
            </h2>
          </div>
        </div>
        <button
          type="button"
          onClick={() => list.refetch()}
          className="btn-secondary"
        >
          <RefreshCcw className="h-3.5 w-3.5" /> Обновить
        </button>
      </div>

      <p className="mb-4 text-sm text-fg-muted">
        Пользователь оплатил, подписка создана, но в момент webhook'а VPN-API
        не ответил — UUID/ключ ещё не выданы. Фоновый воркер дёргает retry
        раз в 5 мин (макс 5 попыток). Здесь можно дёрнуть руками сейчас.
      </p>

      {list.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ShieldOff}
          title="Очередь пуста"
          description="Все оплаченные подписки имеют VPN-ключи. Это норма."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                <th className="px-2 py-2 font-medium">Sub ID</th>
                <th className="px-2 py-2 font-medium">Юзер</th>
                <th className="px-2 py-2 font-medium">Тариф</th>
                <th className="px-2 py-2 font-medium">Попыток</th>
                <th className="px-2 py-2 font-medium">Последняя ошибка</th>
                <th className="px-2 py-2 font-medium">С</th>
                <th className="px-2 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {rows.map((r) => {
                const id = Number(r.id ?? 0);
                const attempts = asNum(r.activation_attempts) ?? 0;
                const err = String(r.last_activation_error ?? "—");
                const sinceStr =
                  typeof r.activated_at === "string"
                    ? fmtDate(r.activated_at)
                    : "—";
                return (
                  <tr
                    key={id || Math.random()}
                    className="hover:bg-bg-elevated/40"
                  >
                    <td className="px-2 py-2 font-mono text-xs text-fg-muted">
                      {id}
                    </td>
                    <td className="px-2 py-2 text-fg">
                      tg:{String(r.telegram_id ?? "—")}
                    </td>
                    <td className="px-2 py-2 text-fg">
                      {String(r.subscription_type ?? "—")}
                    </td>
                    <td className="px-2 py-2">
                      <span
                        className={
                          attempts >= 5
                            ? "badge-danger"
                            : attempts >= 3
                            ? "badge-warning"
                            : "badge-muted"
                        }
                      >
                        {attempts}/5
                      </span>
                    </td>
                    <td className="max-w-[280px] truncate px-2 py-2 text-xs text-fg-muted">
                      {err}
                    </td>
                    <td className="px-2 py-2 text-xs text-fg-muted">
                      {sinceStr}
                    </td>
                    <td className="px-2 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => retry.mutate(id)}
                        disabled={retry.isPending}
                        className="btn-ghost"
                      >
                        {retry.isPending && retry.variables === id ? (
                          <Spinner />
                        ) : (
                          <PlayCircle className="h-3.5 w-3.5" />
                        )}
                        Retry
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function PendingPaymentsSection() {
  const list = useQuery({
    queryKey: ["payments", "pending"],
    queryFn: endpoints.paymentsPending,
    refetchInterval: 15_000,
  });

  return (
    <section className="card p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border">
            <Clock className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Висящие платежи
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Статус «pending»
              {list.data && list.data.length > 0 && (
                <span className="ml-2 badge-warning">
                  {list.data.length}
                </span>
              )}
            </h2>
          </div>
        </div>
        <button
          type="button"
          onClick={() => list.refetch()}
          className="btn-secondary"
        >
          <RefreshCcw className="h-3.5 w-3.5" /> Обновить
        </button>
      </div>

      {list.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          icon={Wallet}
          title="Нет висящих платежей"
          description="Все платежи обработаны. Это хороший признак."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                <th className="px-2 py-2 font-medium">ID</th>
                <th className="px-2 py-2 font-medium">Юзер</th>
                <th className="px-2 py-2 font-medium">Тариф</th>
                <th className="px-2 py-2 font-medium">Сумма</th>
                <th className="px-2 py-2 font-medium">Источник</th>
                <th className="px-2 py-2 font-medium">Создан</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {list.data.map((p) => (
                <tr
                  key={String(p.id ?? Math.random())}
                  className="hover:bg-bg-elevated/40"
                >
                  <td className="px-2 py-2 font-mono text-xs text-fg-muted">
                    {String(p.id ?? "—")}
                  </td>
                  <td className="px-2 py-2 text-fg">
                    tg:{String(p.telegram_id ?? "—")}
                  </td>
                  <td className="px-2 py-2 text-fg">{String(p.tariff ?? "—")}</td>
                  <td className="px-2 py-2 text-fg">
                    {typeof p.amount === "number"
                      ? fmtRub(p.amount / 100)
                      : String(p.amount ?? "—")}
                  </td>
                  <td className="px-2 py-2 text-fg-muted">
                    {String(p.source ?? "—")}
                  </td>
                  <td className="px-2 py-2 text-fg-muted">
                    {typeof p.created_at === "string"
                      ? fmtDate(p.created_at)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-fg-subtle">
            Платежи &ldquo;виснут&rdquo; обычно из-за пропавших webhook'ов от
            провайдера. Большинство решаются ретраем со стороны провайдера в
            течение часа. Если &gt;24 ч — стоит проверить руками. Авто-полл
            раз в 15 секунд (
            {fmtNum(list.data.length)} записей).
          </p>
        </div>
      )}
    </section>
  );
}
