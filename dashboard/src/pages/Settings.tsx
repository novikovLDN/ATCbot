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
import {
  ConfirmDialog,
  EmptyFailure,
  EmptyNotConfigured,
} from "@/components/ui";
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
  type PushSubscriptionRow,
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
      "Push в браузер при сбоях webhook'ов (Platega / CryptoBot / Lava) и любых необработанных исключениях в платёжном потоке. Если push не доставлен — придёт в Telegram.",
    icon: AlertCircle,
  },
  {
    key: "broadcast_done",
    title: "Рассылка завершена",
    description:
      "Push после окончания каждой рассылки с количеством доставленных и упавших сообщений. Если push не доставлен — придёт в Telegram.",
    icon: Megaphone,
  },
  {
    key: "revenue_milestone",
    title: "Дневной доход",
    description:
      "Push с похвалой при пересечении планок 5k / 10k / 15k / 20k / 25k / 30k / 35k ₽ за сутки (МСК). Если push не доставлен — придёт в Telegram.",
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
              Уведомления админу
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Push в браузер, с запасным каналом в Telegram
            </h2>
          </div>
        </div>

        {/* ОТКАЗ ЗАПРОСА РИСУЕТСЯ ОТКАЗОМ, а не переключателями.
            До правки при ошибке ветка уходила в `flags.data ? … : true`, то
            есть все три тумблера показывались ВКЛЮЧЁННЫМИ. Человек видел
            выдуманное состояние настроек и мог выключить то, что и так было
            выключено, — на уведомлениях об ошибках платежей это дорого. */}
        {flags.isError ? (
          <EmptyFailure
            what="настройки уведомлений"
            reason={
              (flags.error as ApiError)?.detail ??
              "Не знаем, что сейчас включено. Показывать тумблеры наугад нельзя — можно выключить нужное."
            }
            onRetry={() => flags.refetch()}
          />
        ) : flags.isLoading ? (
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
  // Устройство, отключение которого подтверждают в диалоге.
  const [toRemove, setToRemove] = useState<PushSubscriptionRow | null>(null);

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

  // Порядок проверок важен. На iPhone PushManager и Notification существуют
  // только в приложении, добавленном на «экран Домой»: во вкладке Safari и в
  // встроенном браузере Telegram их нет, и isPushSupported() возвращает false.
  // Раньше этот случай попадал в ветку «Не поддерживается» с советом «открой
  // в Safari» — то есть пользователю в Safari советовали открыть в Safari, а
  // правильная инструкция стояла ниже по коду и на iPhone была недостижима.
  // Поэтому iOS-случай разбираем ДО общей ветки.
  if (!supported && iosBlocker) {
    return (
      <section className="card p-5">
        <div className="flex items-start gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-warning/15 text-warning">
            <Share className="h-4 w-4" />
          </div>
          <div className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Браузерные уведомления
            </div>
            <h2 className="text-lg font-semibold text-fg">
              Нужно установить как приложение
            </h2>
            <p className="text-sm text-fg-muted">
              На iPhone push работает только у приложения с домашнего экрана —
              это ограничение Apple, обойти его нельзя. Порядок такой:
            </p>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-fg-muted">
              <li>
                Если дашборд открыт из Telegram — нажми «…» и выбери «Открыть
                в Safari». Во встроенном браузере установка недоступна.
              </li>
              <li>В Safari нажми «Поделиться» → «На экран Домой».</li>
              <li>Запусти Atlas Admin с домашнего экрана.</li>
              <li>Вернись на этот экран и нажми «Подключить push».</li>
            </ol>
            <p className="text-sm text-fg-muted">
              Пока push не подключён, важные уведомления приходят в Telegram —
              они не потеряются.
            </p>
          </div>
        </div>
      </section>
    );
  }

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
              Этот браузер не умеет push. Открой дашборд в Safari (iOS / macOS)
              или Chrome. Уведомления при этом продолжают приходить в Telegram.
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

      {/* Список подключённых устройств. При отказе запроса раньше блок просто
          исчезал: понять, что устройств нет и что список не загрузился, было
          нельзя. */}
      {subs.isError && (
        <EmptyFailure
          what="список подключённых устройств"
          reason={
            (subs.error as ApiError)?.detail ??
            "Запрос не вернулся. Пустой список означал бы, что push не подключён нигде."
          }
          onRetry={() => subs.refetch()}
        />
      )}

      {!subs.isError && subs.data && subs.data.length === 0 && (
        <EmptyNotConfigured
          title="Ни одно устройство не подключено"
          description="Нажмите «Подключить на этом устройстве» выше и разрешите уведомления в браузере. Пока push не подключён, важное приходит в Telegram."
          icon={BellRing}
        />
      )}

      {!subs.isError && subs.data && subs.data.length > 0 && (
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
                onClick={() => setToRemove(s)}
                disabled={removeRemote.isPending}
                className="btn-ghost text-danger hover:text-danger"
                aria-label={`Отключить устройство ${s.label || "без имени"}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Системный confirm() заменён на диалог: он называет устройство и
          говорит, что именно перестанет работать. «Удалить это устройство?»
          без имени — вопрос, на который нельзя ответить осознанно, когда
          устройств в списке несколько (research §6.6). */}
      <ConfirmDialog
        open={toRemove !== null}
        onCancel={() => setToRemove(null)}
        onConfirm={() => toRemove && removeRemote.mutate(toRemove.endpoint)}
        title={`Отключить push на устройстве «${toRemove?.label || "без имени"}»?`}
        body="Уведомления туда приходить перестанут. Подключить обратно можно только с самого устройства — с этого экрана чужое устройство не вернуть."
        confirmLabel="Отключить"
        cancelLabel="Оставить"
        destructive
        loading={removeRemote.isPending}
      />
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
