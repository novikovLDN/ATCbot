/**
 * Operational settings rendered under the appearance/brand tiles of
 * SettingsScreen: Telegram DM flags, SBP routing, browser push and a
 * test sender. Each block is its own bento tile.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  BellRing,
  AlertCircle,
  Megaphone,
  TrendingUp,
  Send,
  Smartphone,
  Trash2,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useBranding } from "@/lib/branding";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/Spinner";
import { Bento, SectionHeader, Surface } from "@/components/ui/Surface";
import { IconButton, ListRow, StatusDot, Switch as Toggle } from "@/components/ui/controls";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { toast } from "@/store/toast";
import {
  disablePushOnThisDevice,
  enablePush,
  iosNeedsHomeScreen,
  isPushSupported,
  isStandalonePWA,
  isSubscribedHere,
  listPushSubscriptions,
  sendPushTest,
} from "@/lib/push";
import { Share } from "lucide-react";

interface FlagDescriptor {
  key: "payment_error" | "broadcast_done" | "revenue_milestone";
  title: string;
  description: string;
  icon: typeof Bell;
}

const FLAGS: FlagDescriptor[] = [
  {
    key: "payment_error",
    title: "Ошибки платежей",
    description:
      "DM при сбоях webhook'ов (Platega / CryptoBot / WATA) и любых необработанных исключениях в платёжном потоке.",
    icon: AlertCircle,
  },
  {
    key: "broadcast_done",
    title: "Рассылка завершена",
    description:
      "DM после окончания каждой рассылки с количеством доставленных и упавших сообщений.",
    icon: Megaphone,
  },
  {
    key: "revenue_milestone",
    title: "Дневной доход",
    description:
      "DM с похвалой при пересечении планок 5k / 10k / 15k / 20k / 25k / 30k / 35k ₽ за сутки (UTC).",
    icon: TrendingUp,
  },
];

export function Settings() {
  const qc = useQueryClient();

  const flags = useQuery({
    queryKey: ["settings", "notifications"],
    queryFn: endpoints.settingsNotificationsGet,
  });

  const toggle = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      endpoints.settingsNotificationsPatch(key, enabled),
    onSuccess: (data, vars) => {
      qc.setQueryData(["settings", "notifications"], data);
      toast.success(
        vars.enabled ? "Включено" : "Отключено",
      );
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сохранить"),
  });

  const test = useMutation({
    mutationFn: () => endpoints.settingsTestNotifications(),
    onSuccess: (r) => {
      toast.success(
        `Отправляю ${r.count} тестовых уведомлений (1 с задержкой)`,
      );
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить"),
  });

  return (
    <>
      <SectionHeader title="Уведомления" />
      <Bento>
        <Surface className="sm:col-span-6 xl:col-span-7" label="Telegram DM">
          <h2 className="mb-3 text-[15px] font-semibold">Что присылать в личку</h2>

          {flags.isError && (
            <ErrorState
              className="mb-3 rounded-row bg-tile-3 p-4"
              error={flags.error}
              onRetry={() => flags.refetch()}
            />
          )}

          {flags.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {FLAGS.map((f) => (
                <Skeleton key={f.key} className="h-[72px] w-full rounded-row" />
              ))}
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {FLAGS.map((f) => {
                const enabled = flags.data ? (flags.data[f.key] ?? true) : true;
                const Icon = f.icon;
                return (
                  <li key={f.key} className="list-row items-start py-3 pr-4">
                    <span className="t-mute grid w-5 flex-none place-items-center pt-0.5">
                      <Icon className="h-4 w-4" aria-hidden="true" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-[14px] font-medium">{f.title}</div>
                      <div className="t-mute mt-0.5 text-[12px] leading-4">
                        {f.description}
                      </div>
                    </div>
                    <Toggle
                      label={f.title}
                      checked={enabled}
                      onChange={(v) =>
                        toggle.mutate({ key: f.key, enabled: v })
                      }
                      disabled={toggle.isPending}
                    />
                  </li>
                );
              })}
            </ul>
          )}
        </Surface>

        <SbpRouterSection className="sm:col-span-6 xl:col-span-5" />

        <PushSection className="sm:col-span-6 xl:col-span-7" />

        <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Проверка">
          <h2 className="text-[15px] font-semibold">Тестовые уведомления (Telegram)</h2>
          <p className="t-mute mb-4 mt-1 text-[13px] leading-5">
            Пришлю в личку Telegram по одному примеру каждого типа уведомления
            с интервалом 1 секунда. Просто проверка — никаких событий в боте
            не происходит.
          </p>

          <button
            type="button"
            onClick={() => test.mutate()}
            disabled={test.isPending}
            className="btn-secondary"
          >
            {test.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
            Прислать в Telegram
          </button>
        </Surface>
      </Bento>
    </>
  );
}

function SbpRouterSection({ className }: { className?: string }) {
  const qc = useQueryClient();

  const cfg = useQuery({
    queryKey: ["settings", "sbp-router"],
    queryFn: endpoints.settingsSbpRouterGet,
  });

  const [pendingPct, setPendingPct] = useState<number | null>(null);

  const save = useMutation({
    mutationFn: (v: { mode: "platega" | "wata" | "split"; wata_percent: number }) =>
      endpoints.settingsSbpRouterPatch(v.mode, v.wata_percent),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "sbp-router"], data);
      setPendingPct(null);
      const label =
        data.mode === "platega"
          ? "Platega"
          : data.mode === "wata"
            ? "Wata"
            : `Split ${data.wata_percent}% Wata / ${100 - data.wata_percent}% Platega`;
      toast.success(`СБП-провайдер: ${label}`);
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сохранить"),
  });

  const mode = cfg.data?.mode ?? "platega";
  const wataPct = pendingPct ?? cfg.data?.wata_percent ?? 50;

  return (
    <Surface className={className} variant="raised" label="Платежи">
      <h2 className="text-[15px] font-semibold">СБП-провайдер (live-switch)</h2>
      <p className="t-mute mb-4 mt-1 text-[13px] leading-5">
        Куда уходит кнопка «📱 СБП» в боте: Platega, Wata или 50/50 (случай-но
        распределяется по <code className="font-mono text-[12px]">telegram_id</code>,
        один юзер всегда попадает к одному провайдеру). Переключение —
        мгновенное, без рестарта. Другие процессы бота подхватят через ≤30 сек
        (кэш).
      </p>

      {cfg.isError && (
        <ErrorState
          className="mb-3 rounded-row bg-tile-3 p-4"
          error={cfg.error}
          onRetry={() => cfg.refetch()}
        />
      )}

      {cfg.isLoading ? (
        <div className="grid grid-cols-3 gap-2" role="status" aria-label="Загрузка">
          <Skeleton className="h-[60px] rounded-row" />
          <Skeleton className="h-[60px] rounded-row" />
          <Skeleton className="h-[60px] rounded-row" />
        </div>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-3 gap-2" role="group" aria-label="СБП-провайдер">
            {(
              [
                { key: "platega", label: "Platega", sub: "все → Platega" },
                { key: "wata", label: "Wata", sub: "все → Wata" },
                { key: "split", label: "50/50", sub: "случай-но" },
              ] as const
            ).map((opt) => {
              const active = mode === opt.key;
              return (
                <button
                  key={opt.key}
                  type="button"
                  aria-pressed={active}
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate({
                      mode: opt.key,
                      wata_percent:
                        opt.key === "split" ? wataPct : cfg.data?.wata_percent ?? 50,
                    })
                  }
                  className={cn(
                    "flex min-h-[60px] flex-col items-center justify-center gap-0.5 rounded-row px-3 py-2.5 text-[14px] font-medium transition-colors disabled:opacity-50",
                    active
                      ? "bg-accent text-onaccent"
                      : "bg-tile-3 text-ink hover:bg-tile-4",
                  )}
                >
                  <span>{opt.label}</span>
                  <span className={cn("text-[12px] font-normal", active ? "opacity-70" : "t-mute")}>
                    {opt.sub}
                  </span>
                </button>
              );
            })}
          </div>

          {mode === "split" && (
            <div className="rounded-row bg-tile-3 p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[14px]">
                <span className="font-medium">Процент на Wata</span>
                <span className="tabular t-body">
                  {wataPct}% Wata · {100 - wataPct}% Platega
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={wataPct}
                onChange={(e) => setPendingPct(Number(e.target.value))}
                className="w-full accent-accent"
                aria-label="Процент на Wata"
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="t-mute min-w-0 flex-1 text-[12px] leading-4">
                  Юзеры с <code className="font-mono">telegram_id % 100 &lt; {wataPct}</code> идут в Wata.
                  Один и тот же юзер всегда попадает к одному провайдеру.
                </p>
                {pendingPct !== null &&
                  pendingPct !== (cfg.data?.wata_percent ?? 50) && (
                    <button
                      type="button"
                      disabled={save.isPending}
                      onClick={() =>
                        save.mutate({
                          mode: "split",
                          wata_percent: pendingPct,
                        })
                      }
                      className="btn-primary"
                    >
                      Сохранить {pendingPct}%
                    </button>
                  )}
              </div>
            </div>
          )}
        </>
      )}
    </Surface>
  );
}

function PushSection({ className }: { className?: string }) {
  const brand = useBranding();
  const qc = useQueryClient();
  const supported = isPushSupported();
  const iosBlocker = iosNeedsHomeScreen();
  const standalone = isStandalonePWA();
  const [permission, setPermission] = useState<NotificationPermission>(
    supported ? Notification.permission : "denied",
  );
  const [hereSubscribed, setHereSubscribed] = useState(false);

  useEffect(() => {
    if (!supported) return;
    isSubscribedHere().then(setHereSubscribed);
  }, [supported]);

  const subs = useQuery({
    queryKey: ["push", "subscriptions"],
    queryFn: listPushSubscriptions,
    enabled: supported,
  });

  const enable = useMutation({
    mutationFn: () => enablePush(),
    onSuccess: () => {
      toast.success("Уведомления включены на этом устройстве");
      setHereSubscribed(true);
      setPermission(Notification.permission);
      qc.invalidateQueries({ queryKey: ["push"] });
    },
    onError: (e: unknown) => {
      const msg = (e as Error).message;
      if (msg === "permission_denied") {
        if (iosBlocker) {
          toast.error(
            "На iPhone сначала «Поделиться → На экран Домой», затем открой иконку и подключи push оттуда.",
          );
        } else {
          toast.error("Разрешение на уведомления не дано");
        }
      } else if (msg === "not_supported") {
        toast.error("Браузер не поддерживает push");
      } else {
        toast.error("Не удалось подключить: " + msg);
      }
      setPermission(supported ? Notification.permission : "denied");
    },
  });

  const disable = useMutation({
    mutationFn: () => disablePushOnThisDevice(),
    onSuccess: () => {
      toast.success("Отключено на этом устройстве");
      setHereSubscribed(false);
      qc.invalidateQueries({ queryKey: ["push"] });
    },
    onError: () => toast.error("Не удалось отключить"),
  });

  const removeRemote = useMutation({
    mutationFn: async (endpoint: string) => {
      const { api } = await import("@/lib/api");
      return api.post("/settings/push/unsubscribe", { endpoint });
    },
    onSuccess: () => {
      toast.success("Удалено");
      qc.invalidateQueries({ queryKey: ["push"] });
    },
    onError: () => toast.error("Не удалось удалить"),
  });

  const test = useMutation({
    mutationFn: () => sendPushTest(),
    onSuccess: (r) => {
      if (r.total === 0) {
        toast.info("Нет подключённых устройств");
        return;
      }
      if (r.sent > 0) {
        toast.success(
          `Отправлено ${r.sent} / ${r.total}` +
            (r.removed > 0 ? ` · покинутых ${r.removed}` : ""),
        );
        if (r.failed > 0 && r.errors?.length) {
          const first = r.errors[0];
          toast.error(
            `Часть упала: ${first.host} → ${first.reason}${
              first.status ? ` (${first.status})` : ""
            }`,
          );
        }
        return;
      }
      // sent === 0 — everything failed. Surface the first error so
      // the admin can diagnose (404/410 = "пересоздай подписку",
      // 401/403 = VAPID mismatch, etc.)
      const first = r.errors?.[0];
      if (first) {
        const head = `${first.host || "push"} → ${first.reason}${
          first.status ? ` (HTTP ${first.status})` : ""
        }`;
        const detail = first.detail ? `\n${first.detail}` : "";
        if (r.removed > 0) {
          toast.error(
            "Подписка устарела — переподключи push на этом устройстве. " +
              head +
              detail,
          );
        } else {
          toast.error("Push не прошёл: " + head + detail);
        }
      } else {
        toast.error(`Отправлено 0 / ${r.total}`);
      }
    },
    onError: (e: unknown) =>
      toast.error("Не удалось отправить: " + ((e as Error)?.message ?? "")),
  });

  if (!supported) {
    return (
      <Surface className={className} label="Браузерные уведомления">
        <h2 className="text-[15px] font-semibold">Не поддерживается</h2>
        <p className="t-mute mt-1 text-[13px] leading-5">
          Этот браузер не умеет push. Открой в Safari (iOS / macOS) или
          Chrome.
        </p>
      </Surface>
    );
  }

  return (
    <Surface className={className} label="Браузерные уведомления">
      <h2 className="text-[15px] font-semibold">Push в систему</h2>
      <p className="t-mute mb-4 mt-1 text-[13px] leading-5">
        Когда подключено — события приходят как нативные iOS / macOS / Android
        уведомления. По клику открывается дашборд. Можно подключить разные
        устройства: телефон, ноутбук, планшет.
      </p>

      {iosBlocker && (
        <div className="mb-4 flex items-start gap-3 rounded-row bg-tile-3 p-4 text-[14px]">
          <Share className="t-mute mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold">
              <StatusDot tone="warn" />
              Нужно установить как приложение
            </div>
            <p className="t-body mt-1 text-[13px] leading-5">
              iPhone Safari не умеет push в обычной вкладке. В Safari нажми
              «Поделиться» → «На экран Домой». Затем открой иконку {brand.short}
              Admin с домашнего экрана и подключи push отсюда.
            </p>
          </div>
        </div>
      )}

      {standalone && (
        <div className="mb-4">
          <span className="badge-success">
            <Smartphone className="h-3 w-3" /> Запущено как приложение
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {hereSubscribed ? (
          <>
            <span className="badge-success">
              <BellRing className="h-3 w-3" /> Это устройство подключено
            </span>
            <button
              type="button"
              onClick={() => disable.mutate()}
              disabled={disable.isPending}
              className="btn-secondary"
            >
              {disable.isPending ? <Spinner /> : null}
              Отключить здесь
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => enable.mutate()}
              disabled={enable.isPending || iosBlocker}
              className="btn-primary"
              title={iosBlocker ? "Сначала добавь на экран Домой" : undefined}
            >
              {enable.isPending ? <Spinner /> : <BellRing className="h-3.5 w-3.5" />}
              Подключить на этом устройстве
            </button>
            {permission === "denied" && (
              <span className="badge-danger">Разрешение отозвано</span>
            )}
          </>
        )}
        <button
          type="button"
          onClick={() => test.mutate()}
          disabled={test.isPending}
          className="btn-secondary"
        >
          {test.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
          Прислать тестовый push
        </button>
      </div>

      {subs.data && subs.data.length > 0 && (
        <>
          <p className="t-mute mb-2 mt-5 text-[13px]">Подключённые устройства</p>
          <ul className="flex flex-col gap-2">
            {subs.data.map((s) => (
              <li key={s.id}>
                <ListRow
                  leading={<Smartphone className="t-mute h-4 w-4" aria-hidden="true" />}
                  title={s.label || "Устройство"}
                  meta={
                    (s.user_agent
                      ? s.user_agent.slice(0, 80)
                      : new URL(s.endpoint).host) +
                    (s.created_at
                      ? ` · добавлено ${new Date(s.created_at).toLocaleDateString("ru-RU")}`
                      : "") +
                    (s.last_used_at
                      ? ` · использовано ${new Date(s.last_used_at).toLocaleDateString("ru-RU")}`
                      : "")
                  }
                  trailing={
                    <IconButton
                      small
                      label="Удалить устройство"
                      className="text-danger"
                      onClick={() => {
                        if (confirm("Удалить это устройство?")) {
                          removeRemote.mutate(s.endpoint);
                        }
                      }}
                      disabled={removeRemote.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </IconButton>
                  }
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Surface>
  );
}

