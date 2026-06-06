import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  AlertCircle,
  Megaphone,
  TrendingUp,
  Send,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { Spinner } from "@/components/Spinner";
import { toast } from "@/store/toast";

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
              Тестовые уведомления
            </h2>
          </div>
        </div>

        <p className="mb-4 text-sm text-fg-muted">
          Пришлю в личку по одному примеру каждого типа уведомления (ошибка
          платежа, рассылка завершена, планка дохода) с интервалом 1 секунда.
          Просто проверка — никаких событий в боте не происходит.
        </p>

        <button
          type="button"
          onClick={() => test.mutate()}
          disabled={test.isPending}
          className="btn-primary"
        >
          {test.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
          Прислать тестовые
        </button>
      </section>
    </div>
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
