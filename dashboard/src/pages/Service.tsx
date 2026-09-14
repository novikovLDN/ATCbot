import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Power, RefreshCcw, Save, PlayCircle, Plus, Trash2 } from "lucide-react";
import { isInAppBrowser, isPasskeySupported, passkeyList, passkeyDelete, registerPasskey, type PasskeyRow } from "@/lib/passkey";
import { ApiError, endpoints } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton, ListRow, StatusDot } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

export function Service() {
  return (
    <>
      <PageHeader title="Сервис" sub="Операции: passkey, режим инцидента, очередь провизии VPN и висящие платежи." />
      <Bento>
        <PasskeysSection />
        <IncidentSection />
        <PendingActivationsSection />
        <PendingPaymentsSection />
      </Bento>
    </>
  );
}

function RowsSkeleton() {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-2/3" />
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
    <Surface
      className="sm:col-span-6 xl:col-span-7"
      variant={isActive ? "raised" : "ink"}
      label="Режим инцидента"
      aside={
        isActive ? (
          <span className="badge-warning">
            <StatusDot tone="warn" /> Включён
          </span>
        ) : (
          <span className="badge-muted">Выключен</span>
        )
      }
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-60">
          <h2 className="text-[15px] font-semibold">Баннер всем пользователям</h2>
          <p className="t-mute mt-1 text-[13px] leading-5">
            Текст появится у каждого юзера на главном экране бота. Используй
            для предупреждений о тех. работах, перебоях оплаты, и т.п.
          </p>
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
        <span className="t-mute mb-1.5 block text-[13px]">Текст (HTML)</span>
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
        <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
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
    </Surface>
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
    <Surface
      className="sm:col-span-6 xl:col-span-12"
      label="Очередь провизии VPN"
      aside={
        <div className="flex items-center gap-2">
          {total > 0 && <span className="badge-warning tabular">{fmtNum(total)}</span>}
          <button type="button" onClick={() => list.refetch()} className="btn-secondary">
            <RefreshCcw className="h-3.5 w-3.5" /> Обновить
          </button>
        </div>
      }
    >
      <p className="t-mute mb-4 max-w-[80ch] text-[13px] leading-5">
        Пользователь оплатил, подписка создана, но в момент webhook'а VPN-API
        не ответил — UUID/ключ ещё не выданы. Фоновый воркер дёргает retry
        раз в 5 мин (макс 5 попыток). Здесь можно дёрнуть руками сейчас.
      </p>

      {list.isLoading ? (
        <RowsSkeleton />
      ) : list.isError && !list.data ? (
        <ErrorState className="bg-tile-2" error={list.error} onRetry={() => list.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Очередь пуста"
          hint="Все оплаченные подписки имеют VPN-ключи. Это норма."
        />
      ) : (
        <div className="-mx-2 overflow-x-auto px-2">
          <table className="dtable min-w-[760px]">
            <thead>
              <tr>
                <th>Sub ID</th>
                <th>Юзер</th>
                <th>Тариф</th>
                <th>Попыток</th>
                <th>Последняя ошибка</th>
                <th>С</th>
                <th aria-label="Действия"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const id = Number(r.id ?? 0);
                const attempts = asNum(r.activation_attempts) ?? 0;
                const err = String(r.last_activation_error ?? "—");
                const sinceStr =
                  typeof r.activated_at === "string"
                    ? fmtDate(r.activated_at)
                    : "—";
                return (
                  <tr key={id || Math.random()}>
                    <td className="t-mute tabular font-mono text-[12px]">{id}</td>
                    <td className="tabular">tg:{String(r.telegram_id ?? "—")}</td>
                    <td>{String(r.subscription_type ?? "—")}</td>
                    <td>
                      <span
                        className={
                          attempts >= 5
                            ? "badge-danger tabular"
                            : attempts >= 3
                            ? "badge-warning tabular"
                            : "badge-muted tabular"
                        }
                      >
                        {attempts}/5
                      </span>
                    </td>
                    <td className="t-mute max-w-[280px] truncate text-[12px]" title={err}>
                      {err}
                    </td>
                    <td className="t-mute tabular whitespace-nowrap text-[12px]">{sinceStr}</td>
                    <td className="text-right">
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
    </Surface>
  );
}

function PendingPaymentsSection() {
  const list = useQuery({
    queryKey: ["payments", "pending"],
    queryFn: endpoints.paymentsPending,
    refetchInterval: 15_000,
  });

  return (
    <Surface
      className="sm:col-span-6 xl:col-span-12"
      variant="raised"
      label="Висящие платежи · статус «pending»"
      aside={
        <div className="flex items-center gap-2">
          {list.data && list.data.length > 0 && (
            <span className="badge-warning tabular">{list.data.length}</span>
          )}
          <button type="button" onClick={() => list.refetch()} className="btn-secondary">
            <RefreshCcw className="h-3.5 w-3.5" /> Обновить
          </button>
        </div>
      }
    >
      {list.isLoading ? (
        <RowsSkeleton />
      ) : list.isError && !list.data ? (
        <ErrorState className="bg-tile-3" error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="Нет висящих платежей"
          hint="Все платежи обработаны. Это хороший признак."
        />
      ) : (
        <>
          <div className="-mx-2 overflow-x-auto px-2">
            <table className="dtable min-w-[640px]">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Юзер</th>
                  <th>Тариф</th>
                  <th className="num">Сумма</th>
                  <th>Источник</th>
                  <th>Создан</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((p) => (
                  <tr key={String(p.id ?? Math.random())}>
                    <td className="t-mute tabular font-mono text-[12px]">{String(p.id ?? "—")}</td>
                    <td className="tabular">tg:{String(p.telegram_id ?? "—")}</td>
                    <td>{String(p.tariff ?? "—")}</td>
                    <td className="num">
                      {typeof p.amount === "number"
                        ? fmtRub(p.amount / 100)
                        : String(p.amount ?? "—")}
                    </td>
                    <td className="t-mute">{String(p.source ?? "—")}</td>
                    <td className="t-mute tabular whitespace-nowrap">
                      {typeof p.created_at === "string"
                        ? fmtDate(p.created_at)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="t-mute mt-3 max-w-[80ch] text-[12px] leading-5">
            Платежи &ldquo;виснут&rdquo; обычно из-за пропавших webhook'ов от
            провайдера. Большинство решаются ретраем со стороны провайдера в
            течение часа. Если &gt;24 ч — стоит проверить руками. Авто-полл
            раз в 15 секунд (
            {fmtNum(list.data.length)} записей).
          </p>
        </>
      )}
    </Surface>
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

function PasskeysSection() {
  const qc = useQueryClient();
  const supported = isPasskeySupported();

  const list = useQuery({
    queryKey: ["passkeys", "list"],
    queryFn: passkeyList,
    enabled: supported,
  });

  const [label, setLabel] = useState("");
  const add = useMutation({
    mutationFn: () => registerPasskey(label.trim() || undefined),
    onSuccess: () => {
      toast.success("Passkey добавлен");
      setLabel("");
      qc.invalidateQueries({ queryKey: ["passkeys"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { detail?: string })?.detail;
      if (detail !== "cancelled") {
        toast.error(detail ?? "Не удалось добавить");
      }
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => passkeyDelete(id),
    onSuccess: () => {
      toast.success("Удалён");
      qc.invalidateQueries({ queryKey: ["passkeys"] });
    },
    onError: () => toast.error("Не удалось удалить"),
  });

  if (!supported) {
    return (
      <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Passkey">
        <h2 className="text-[15px] font-semibold">Браузер не поддерживает</h2>
        <p className="t-mute mt-1 text-[13px] leading-5">
          {isInAppBrowser()
            ? "Это встроенный браузер приложения (Telegram и т.п.) — iOS не даёт ему ключи входа. Открой дашборд в Safari («⋯» → «Открыть в Safari») и добавь passkey там."
            : "Открой дашборд в Safari (iOS / macOS) или Chrome — там доступны Face ID / Touch ID / системные ключи."}
        </p>
      </Surface>
    );
  }

  return (
    <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Passkey">
      <h2 className="text-[15px] font-semibold">Face ID / Touch ID</h2>
      <p className="t-mute mb-4 mt-1 text-[13px] leading-5">
        Добавь passkey — и в следующий раз войдёшь одним касанием без
        пароля. Привяжется к этому устройству / iCloud Keychain.
      </p>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto]">
        <input
          className="input"
          maxLength={64}
          placeholder='Метка — напр. "iPhone 15", "MacBook Air"'
          aria-label="Метка passkey"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button
          type="button"
          onClick={() => add.mutate()}
          disabled={add.isPending}
          className="btn-primary"
        >
          {add.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
          Добавить passkey
        </button>
      </div>

      {list.isLoading ? (
        <div className="mt-4">
          <RowsSkeleton />
        </div>
      ) : list.data && list.data.length > 0 ? (
        <ul className="mt-4 flex flex-col gap-2">
          {list.data.map((p: PasskeyRow) => (
            <li key={p.id}>
              <ListRow
                title={p.label || "Passkey"}
                meta={
                  <>
                    {p.transports && p.transports.length > 0 ? (
                      <span>{p.transports.join(" · ")} · </span>
                    ) : null}
                    Добавлен {p.created_at ? new Date(p.created_at).toLocaleDateString("ru-RU") : "—"}
                    {p.last_used_at
                      ? ` · последний вход ${new Date(p.last_used_at).toLocaleDateString("ru-RU")}`
                      : " · ещё не использовался"}
                  </>
                }
                trailing={
                  <IconButton
                    label={`Удалить «${p.label || "passkey"}»`}
                    small
                    className="text-danger"
                    onClick={() => {
                      if (confirm(`Удалить «${p.label || "passkey"}»?`)) del.mutate(p.id);
                    }}
                    disabled={del.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </IconButton>
                }
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="t-mute mt-4 text-[13px]">Ещё ни одного passkey не привязано.</p>
      )}
    </Surface>
  );
}
