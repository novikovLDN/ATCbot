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
import { Spinner } from "@/components/Spinner";
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
      "DM при сбоях webhook'ов (Platega / CryptoBot / Lava) и любых необработанных исключениях в платёжном потоке.",
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
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Настройки
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Уведомления
        </h1>
      </header>

      <section className="card p-5">
        <div className="mb-4 flex items-center gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent">
            <Bell className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Telegram DM
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Что присылать в личку
            </h2>
          </div>
        </div>

        {flags.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {FLAGS.map((f) => {
              const enabled = flags.data ? (flags.data[f.key] ?? true) : true;
              const Icon = f.icon;
              return (
                <li
                  key={f.key}
                  className="flex items-start gap-3 py-4 text-sm"
                >
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-fg">{f.title}</div>
                    <div className="mt-1 text-xs text-fg-muted">
                      {f.description}
                    </div>
                  </div>
                  <Toggle
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
      </section>

      <PushSection />

      <section className="card p-5">
        <div className="mb-3 flex items-center gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border">
            <Send className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Проверка
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Тестовые уведомления (Telegram)
            </h2>
          </div>
        </div>

        <p className="mb-4 text-sm text-fg-muted">
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
      </section>
    </div>
  );
}

function PushSection() {
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
      if (r.sent === 0 && r.total === 0) {
        toast.info("Нет подключённых устройств");
      } else {
        toast.success(
          `Отправлено ${r.sent} / ${r.total}` +
            (r.removed > 0 ? ` · покинутых ${r.removed}` : ""),
        );
      }
    },
    onError: () => toast.error("Не удалось отправить"),
  });

  if (!supported) {
    return (
      <section className="card p-5">
        <div className="flex items-start gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border">
            <BellRing className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Браузерные уведомления
            </div>
            <h2 className="text-lg font-semibold text-fg">Не поддерживается</h2>
            <p className="mt-1 text-sm text-fg-muted">
              Этот браузер не умеет push. Открой в Safari (iOS / macOS) или
              Chrome.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="card p-5">
      <div className="mb-4 flex items-center gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent">
          <BellRing className="h-4 w-4" />
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Браузерные уведомления
          </div>
          <h2 className="text-lg font-semibold text-fg">Push в систему</h2>
        </div>
      </div>

      <p className="mb-4 text-sm text-fg-muted">
        Когда подключено — события приходят как нативные iOS / macOS / Android
        уведомления. По клику открывается дашборд. Можно подключить разные
        устройства: телефон, ноутбук, планшет.
      </p>

      {iosBlocker && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-warning">
          <Share className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <div className="font-semibold">Нужно установить как приложение</div>
            <div className="text-warning/90">
              iPhone Safari не умеет push в обычной вкладке. В Safari нажми
              «Поделиться» → «На экран Домой». Затем открой иконку Atlas
              Admin с домашнего экрана и подключи push отсюда.
            </div>
          </div>
        </div>
      )}

      {standalone && (
        <div className="mb-4 flex items-center gap-2 text-xs text-success">
          <span className="badge-success">
            <Smartphone className="h-3 w-3" /> Запущено как приложение
          </span>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
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
        <ul className="divide-y divide-border/60">
          {subs.data.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-3 py-3 text-sm"
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                <Smartphone className="h-3.5 w-3.5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-fg">
                  {s.label || "Устройство"}
                </div>
                <div className="mt-0.5 text-xs text-fg-muted">
                  {s.user_agent
                    ? s.user_agent.slice(0, 80)
                    : new URL(s.endpoint).host}
                  {s.created_at
                    ? ` · добавлено ${new Date(s.created_at).toLocaleDateString("ru-RU")}`
                    : ""}
                  {s.last_used_at
                    ? ` · использовано ${new Date(s.last_used_at).toLocaleDateString("ru-RU")}`
                    : ""}
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (confirm("Удалить это устройство?")) {
                    removeRemote.mutate(s.endpoint);
                  }
                }}
                disabled={removeRemote.isPending}
                className="btn-ghost text-danger hover:text-danger"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      disabled={disabled}
      className={
        "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:opacity-50 " +
        (checked ? "bg-accent" : "bg-bg-elevated ring-1 ring-border")
      }
    >
      <span
        className={
          "inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform " +
          (checked ? "translate-x-5" : "translate-x-0.5")
        }
      />
    </button>
  );
}
