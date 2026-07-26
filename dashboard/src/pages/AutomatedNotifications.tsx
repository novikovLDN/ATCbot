import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Check,
  ChevronDown,
  Edit3,
  Info,
  Plus,
  Power,
  RefreshCcw,
  RotateCcw,
  Send,
  Timer,
  Trash2,
  X,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { Spinner } from "@/components/Spinner";
import { toast } from "@/store/toast";
import { fmtDate, fmtNum } from "@/lib/format";
import { Collapsible } from "@/components/Collapsible";

interface NotifRow {
  key: string;
  title: string;
  description: string | null;
  category: string;
  is_enabled: boolean;
  has_custom_text: boolean;
  default_text_ru: string;
  custom_text_ru: string | null;
  trigger_config: Record<string, unknown>;
  template_vars: string[];
  updated_at: string | null;
  last_edited_by: number | null;
  is_code_registered: boolean;
}

const CATEGORY_LABEL: Record<string, string> = {
  trial: "🎁 Триал",
  subscription: "💳 Подписка",
  welcome: "👋 Приветствие",
  payment: "💰 Платежи",
  referral: "🤝 Рефералы",
  gift: "🎉 Подарки",
  reminder: "🔔 Напоминания",
  other: "📦 Прочее",
};

export function AutomatedNotifications() {
  const list = useQuery({
    queryKey: ["automated-notifications"],
    queryFn: () => endpoints.automatedNotifications(),
    refetchInterval: 30_000,
  });
  const [editing, setEditing] = useState<NotifRow | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const grouped = useMemo(() => {
    if (!list.data) return new Map<string, NotifRow[]>();
    const m = new Map<string, NotifRow[]>();
    for (const n of list.data) {
      const g = m.get(n.category) ?? [];
      g.push(n);
      m.set(n.category, g);
    }
    // sort inside each group by key
    for (const arr of m.values()) arr.sort((a, b) => a.key.localeCompare(b.key));
    return m;
  }, [list.data]);

  const catOrder = [
    "trial",
    "subscription",
    "reminder",
    "welcome",
    "payment",
    "referral",
    "gift",
    "other",
  ];

  const totalEnabled = list.data?.filter((n) => n.is_enabled).length ?? 0;
  const totalDisabled = list.data?.filter((n) => !n.is_enabled).length ?? 0;
  const totalCustom = list.data?.filter((n) => n.has_custom_text).length ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-4 pb-8 pt-2 md:pt-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-fg">
            <Bell className="h-5 w-5 text-fg-muted" />
            Автоуведомления
          </h1>
          <p className="mt-1 max-w-xl text-sm text-fg-muted">
            Все автоматические сообщения, которые бот шлёт пользователям
            по расписанию (reminders, приветствия, оффер-триггеры).
            Здесь можно править текст, отключать и менять окно отправки —
            <b> без релиза</b>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="btn-primary"
            title="Создать своё уведомление (только редактируемое, без code-триггера)"
          >
            <Plus className="h-3.5 w-3.5" /> Новое
          </button>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-secondary"
            disabled={list.isFetching}
          >
            {list.isFetching ? <Spinner /> : <RefreshCcw className="h-3.5 w-3.5" />}
            Обновить
          </button>
        </div>
      </header>

      {/* KPI-сводка */}
      <div className="grid grid-cols-3 gap-2">
        <SummaryTile
          label="Активных"
          value={fmtNum(totalEnabled)}
          icon={Check}
          tone="success"
        />
        <SummaryTile
          label="Отключено"
          value={fmtNum(totalDisabled)}
          icon={X}
          tone="danger"
        />
        <SummaryTile
          label="С кастомным текстом"
          value={fmtNum(totalCustom)}
          icon={Edit3}
        />
      </div>

      {/* Пояснение как читать колонки */}
      <div className="rounded-xl border border-info/20 bg-info/[0.06] p-3 text-[12px] leading-relaxed text-fg-muted">
        <Info className="mr-1 inline h-3.5 w-3.5 align-[-2px] text-info" />
        <b className="text-info">Подсказки.</b>{" "}
        <b>Тумблер</b> — включить/отключить уведомление
        (немедленное действие, следующий тик планировщика уже пропустит).
        {" · "}
        <b>Изменить</b> — правка текста и окна отправки (для reminder'ов —
        сколько часов до истечения, ±допуск).
        {" · "}
        <b>Сброс</b> — вернуть заводской текст из кода.
      </div>

      {list.isLoading ? (
        <div className="card flex items-center gap-2 p-6 text-sm text-fg-muted">
          <Spinner /> Загружаю список…
        </div>
      ) : list.isError ? (
        <div className="card p-4 text-sm text-danger">
          Не удалось загрузить: {(list.error as ApiError)?.detail ?? "ошибка"}
        </div>
      ) : (
        <div className="space-y-2">
          {catOrder.map((cat) => {
            const rows = grouped.get(cat);
            if (!rows || rows.length === 0) return null;
            const active = rows.filter((r) => r.is_enabled).length;
            return (
              <Collapsible
                key={cat}
                title={CATEGORY_LABEL[cat] ?? cat}
                subtitle={`${rows.length} уведомлений · активных: ${active}`}
                defaultOpen={cat === "trial"}
                remember={`autonotif-cat-${cat}`}
              >
                <div className="mt-2 space-y-2">
                  {rows.map((n) => (
                    <NotificationRow
                      key={n.key}
                      row={n}
                      onEdit={() => setEditing(n)}
                    />
                  ))}
                </div>
              </Collapsible>
            );
          })}
        </div>
      )}

      {editing && (
        <EditModal
          row={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {showCreate && (
        <CreateModal onClose={() => setShowCreate(false)} />
      )}
    </div>
  );
}

function CreateModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("admin.");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("other");
  const [text, setText] = useState("");

  const create = useMutation({
    mutationFn: () => {
      const cleanedKey = key.trim().toLowerCase();
      if (!/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(cleanedKey)) {
        throw new Error("key: формат 'namespace.name' (a-z, 0-9, _)");
      }
      if (title.trim().length < 2) throw new Error("Заголовок ≥ 2 символа");
      if (text.trim().length < 1) throw new Error("Текст не должен быть пустым");
      return endpoints.automatedNotificationCreate({
        key: cleanedKey,
        title: title.trim(),
        description: description.trim() || undefined,
        category,
        default_text_ru: text,
      });
    },
    onSuccess: () => {
      toast.success("Создано");
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) => {
      const msg =
        (e as ApiError)?.detail ??
        (e instanceof Error ? e.message : "Не удалось создать");
      toast.error(msg);
    },
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="card flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden p-0">
        <div className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div>
            <div className="flex items-center gap-2">
              <Plus className="h-4 w-4 text-fg-muted" />
              <h3 className="text-base font-semibold text-fg">
                Новое уведомление
              </h3>
            </div>
            <div className="mt-0.5 text-[11px] text-fg-subtle">
              Создастся редактируемое admin-notification без code-триггера.
              Отправка — только вручную через «Тест в TG».
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          <div className="rounded-lg border border-info/20 bg-info/[0.06] p-3 text-[12px] leading-relaxed text-fg-muted">
            <Info className="mr-1 inline h-3.5 w-3.5 align-[-2px] text-info" />
            <b>Как использовать:</b> admin-notification — заготовка текста
            для будущих кампаний или пробных отправок. Bot не шлёт их
            автоматически (нет code-триггера), но их можно править,
            отправлять себе тестом (кнопка «Тест в TG») и использовать
            через API из своих скриптов.
          </div>

          <label className="block">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              Ключ (уникальный)
            </div>
            <input
              type="text"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="admin.summer_sale"
              className="input font-mono text-[13px]"
              autoFocus
            />
            <div className="mt-1 text-[10px] text-fg-subtle">
              Формат: <code>namespace.name</code> · a-z, 0-9, _ · например,{" "}
              <code>admin.welcome_pro</code>. Namespace <code>admin.</code>{" "}
              рекомендуется, чтобы отличать от зашитых в коде.
            </div>
          </label>

          <label className="block">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              Заголовок (что видит админ)
            </div>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Летняя акция — welcome"
              className="input"
            />
          </label>

          <label className="block">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              Описание (опционально)
            </div>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Для когорты новых юзеров, летний оффер"
              className="input"
            />
          </label>

          <label className="block">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              Категория
            </div>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="input"
            >
              {[
                ["trial", "🎁 Триал"],
                ["subscription", "💳 Подписка"],
                ["welcome", "👋 Приветствие"],
                ["payment", "💰 Платежи"],
                ["referral", "🤝 Рефералы"],
                ["gift", "🎉 Подарки"],
                ["reminder", "🔔 Напоминания"],
                ["other", "📦 Прочее"],
              ].map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              Текст сообщения (HTML)
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              placeholder="🎁 <b>Летняя акция!</b>&#10;&#10;Скидка 20% на любой тариф до конца недели."
              className="input font-mono text-[12px] leading-relaxed"
            />
            <div className="mt-1 text-[10px] text-fg-subtle">
              Поддерживаются HTML-теги Telegram: <code>&lt;b&gt;</code>,{" "}
              <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>,{" "}
              <code>&lt;blockquote&gt;</code>. Эмодзи и{" "}
              <code>&lt;tg-emoji&gt;</code> — тоже.
            </div>
          </label>
        </div>

        <div className="flex items-center gap-2 border-t border-border p-4">
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary flex-1"
            disabled={create.isPending}
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="btn-primary flex-1"
          >
            {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
            Создать
          </button>
        </div>
      </div>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  icon: typeof Bell;
  tone?: "success" | "danger";
}) {
  const textCls =
    tone === "success"
      ? "text-success"
      : tone === "danger"
      ? "text-danger"
      : "text-fg";
  return (
    <div className="rounded-xl border border-border bg-bg-card p-3">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className={`mt-1 text-lg font-semibold tabular-nums ${textCls}`}>
        {value}
      </div>
    </div>
  );
}

function NotificationRow({
  row,
  onEdit,
}: {
  row: NotifRow;
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () =>
      endpoints.automatedNotificationPatch(row.key, {
        is_enabled: !row.is_enabled,
      }),
    onSuccess: () => {
      toast.success(row.is_enabled ? "Отключено" : "Включено");
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось изменить"),
  });

  const trig = row.trigger_config as {
    before_expiry_hours?: number;
    tolerance_hours?: number;
  };
  const triggerLabel = trig?.before_expiry_hours
    ? `за ${trig.before_expiry_hours}ч ±${trig.tolerance_hours ?? 1}ч`
    : "";

  return (
    <div
      className={
        row.is_enabled
          ? "flex items-start gap-3 rounded-xl border border-border bg-bg-card p-3"
          : "flex items-start gap-3 rounded-xl border border-border/60 bg-bg-subtle/40 p-3 opacity-70"
      }
    >
      <button
        type="button"
        onClick={() => toggle.mutate()}
        disabled={toggle.isPending}
        title={row.is_enabled ? "Отключить" : "Включить"}
        className={
          row.is_enabled
            ? "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-success/15 text-success transition hover:bg-success/25"
            : "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-fg/5 text-fg-subtle transition hover:bg-fg/10"
        }
      >
        <Power className="h-4 w-4" />
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-semibold text-fg">{row.title}</span>
          {row.has_custom_text && (
            <span className="rounded-md bg-info/15 px-1.5 py-0.5 text-[10px] font-semibold text-info">
              CUSTOM
            </span>
          )}
          {!row.is_enabled && (
            <span className="rounded-md bg-danger/10 px-1.5 py-0.5 text-[10px] font-semibold text-danger">
              OFF
            </span>
          )}
        </div>
        <div className="mt-0.5 text-[11px] text-fg-muted">
          <code className="rounded bg-fg/5 px-1 py-0.5 text-[10px]">
            {row.key}
          </code>
          {triggerLabel && (
            <span className="ml-2 inline-flex items-center gap-0.5">
              <Timer className="h-3 w-3" /> {triggerLabel}
            </span>
          )}
          {row.updated_at && (
            <span className="ml-2 text-fg-subtle">
              · изменено {fmtDate(row.updated_at)}
            </span>
          )}
        </div>
        {row.description && (
          <div className="mt-1 text-[11px] leading-relaxed text-fg-subtle">
            {row.description}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="btn-secondary shrink-0 self-center"
      >
        <Edit3 className="h-3.5 w-3.5" /> Изменить
      </button>
    </div>
  );
}

function EditModal({ row, onClose }: { row: NotifRow; onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState<string>(
    row.custom_text_ru ?? row.default_text_ru,
  );
  const trig = row.trigger_config as {
    before_expiry_hours?: number;
    tolerance_hours?: number;
    segment_filter?: string;
  };
  const [beforeH, setBeforeH] = useState<string>(
    trig?.before_expiry_hours != null ? String(trig.before_expiry_hours) : "",
  );
  const [tolH, setTolH] = useState<string>(
    trig?.tolerance_hours != null ? String(trig.tolerance_hours) : "",
  );
  const [segmentFilter, setSegmentFilter] = useState<string>(
    trig?.segment_filter ?? "",
  );

  const isReminderConfig = trig?.before_expiry_hours != null;

  // Список всех admin-сегментов для segment_filter dropdown.
  // Загружается лениво (только когда модалка открыта).
  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: () => endpoints.broadcastSegments(),
    staleTime: 60_000,
  });

  const save = useMutation({
    mutationFn: () => {
      const body: {
        custom_text_ru?: string;
        trigger_config?: Record<string, unknown>;
      } = {};
      // Кастомный текст: если совпал с default → отправим "" (reset).
      if (text.trim() !== (row.default_text_ru ?? "").trim()) {
        body.custom_text_ru = text;
      }
      if (isReminderConfig) {
        const b = parseFloat(beforeH);
        const t = parseFloat(tolH);
        if (!Number.isFinite(b) || b <= 0) {
          throw new Error("before_expiry_hours должен быть положительным");
        }
        body.trigger_config = {
          before_expiry_hours: b,
          tolerance_hours: Number.isFinite(t) ? t : 1,
          // Пустая строка = снять фильтр (backend приравнивает к None).
          segment_filter: segmentFilter,
        };
      }
      if (Object.keys(body).length === 0) {
        throw new Error("Нечего сохранять — ничего не изменилось");
      }
      return endpoints.automatedNotificationPatch(row.key, body);
    },
    onSuccess: () => {
      toast.success("Сохранено");
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) => {
      const msg =
        (e as ApiError)?.detail ??
        (e instanceof Error ? e.message : "Не удалось сохранить");
      toast.error(msg);
    },
  });

  const reset = useMutation({
    mutationFn: () => endpoints.automatedNotificationReset(row.key),
    onSuccess: () => {
      toast.success("Сброшено к дефолту");
      setText(row.default_text_ru);
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сбросить"),
  });

  const stats = useQuery({
    queryKey: ["automated-notifications", "stats", row.key],
    queryFn: () => endpoints.automatedNotificationStats(row.key, 168),
    refetchInterval: 60_000,
  });

  const testSend = useMutation({
    mutationFn: () => endpoints.automatedNotificationTestSend(row.key),
    onSuccess: (res) => {
      toast.success(`✅ Отправлено в Telegram (tg:${res.sent_to})`);
    },
    onError: (e: unknown) => {
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить тест");
    },
  });

  const del = useMutation({
    mutationFn: () => endpoints.automatedNotificationDelete(row.key),
    onSuccess: () => {
      toast.success("Удалено");
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) => {
      toast.error((e as ApiError)?.detail ?? "Не удалось удалить");
    },
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="card flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden p-0">
        <div className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div>
            <div className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-fg-muted" />
              <h3 className="text-base font-semibold text-fg">{row.title}</h3>
            </div>
            <div className="mt-0.5 text-[11px] text-fg-subtle">
              <code>{row.key}</code>
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {row.description && (
            <div className="rounded-lg border border-info/20 bg-info/[0.06] p-3 text-[12px] leading-relaxed text-fg-muted">
              <Info className="mr-1 inline h-3.5 w-3.5 align-[-2px] text-info" />
              {row.description}
            </div>
          )}

          {/* Stats за 7 дней */}
          <div className="grid grid-cols-4 gap-2">
            <StatMini label="Отправлено" value={stats.data?.sent ?? 0} tone="success" />
            <StatMini label="Ошибки" value={stats.data?.failed ?? 0} tone="danger" />
            <StatMini label="Заблокировали" value={stats.data?.blocked ?? 0} tone="danger" />
            <StatMini label="Пропущено" value={stats.data?.skipped ?? 0} />
          </div>
          <div className="text-[10px] text-fg-subtle">
            Статистика за последние 7 дней. «Пропущено» — юзеры для которых
            это уведомление было отключено на момент срабатывания триггера.
          </div>

          {/* Trigger config (для reminder-типа) */}
          {isReminderConfig && (
            <div className="rounded-xl border border-border bg-bg-subtle/40 p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wider text-fg-subtle">
                Окно отправки
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <div className="mb-1 text-[11px] text-fg-muted">
                    За сколько часов до истечения
                  </div>
                  <input
                    type="number"
                    value={beforeH}
                    onChange={(e) => setBeforeH(e.target.value)}
                    min={0.1}
                    max={720}
                    step={0.5}
                    className="input"
                  />
                </label>
                <label className="block">
                  <div className="mb-1 text-[11px] text-fg-muted">
                    Допуск (±часов)
                  </div>
                  <input
                    type="number"
                    value={tolH}
                    onChange={(e) => setTolH(e.target.value)}
                    min={0}
                    max={48}
                    step={0.5}
                    className="input"
                  />
                </label>
              </div>
              <div className="mt-2 text-[10px] text-fg-subtle">
                Планировщик проверяет юзеров раз в минуту. Меньший допуск —
                более точное окно, но выше риск пропустить (если worker
                опоздает). 1ч по умолчанию — надёжный баланс.
              </div>

              <div className="mt-3 border-t border-border/60 pt-3">
                <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
                  Дополнительный фильтр аудитории (опционально)
                </div>
                <select
                  value={segmentFilter}
                  onChange={(e) => setSegmentFilter(e.target.value)}
                  className="input"
                  disabled={segments.isLoading}
                >
                  <option value="">
                    — Без фильтра (шлём всем кто попал в окно) —
                  </option>
                  {(segments.data ?? []).map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.group ? `[${s.group}] ` : ""}
                      {s.label} · {s.count} чел
                    </option>
                  ))}
                </select>
                <div className="mt-1 text-[10px] text-fg-subtle">
                  Если задан — reminder уйдёт только тем, кто ЕЩЁ и
                  входит в этот сегмент на момент срабатывания триггера.
                  Полезно, например, для «7д до конца, но только тем, кто
                  никогда не покупал» — узкий таргет.
                </div>
              </div>
            </div>
          )}

          {/* Text editor */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <div className="text-[11px] uppercase tracking-wider text-fg-subtle">
                Текст сообщения (HTML)
              </div>
              <button
                type="button"
                onClick={() => reset.mutate()}
                disabled={reset.isPending || !row.has_custom_text}
                className="btn-ghost text-[11px]"
                title="Восстановить заводской текст"
              >
                <RotateCcw className="h-3 w-3" /> Сброс к дефолту
              </button>
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={10}
              className="input font-mono text-[12px] leading-relaxed"
            />
            <div className="mt-1 text-[10px] text-fg-subtle">
              Поддерживаются HTML-теги Telegram: <code>&lt;b&gt;</code>,{" "}
              <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>,{" "}
              <code>&lt;blockquote&gt;</code>, <code>&lt;a href&gt;</code>.
              Эмодзи и <code>&lt;tg-emoji&gt;</code> — тоже.
              {row.template_vars.length > 0 && (
                <>
                  {" · "}
                  Доступные плейсхолдеры:{" "}
                  {row.template_vars.map((v, i) => (
                    <span key={v}>
                      <code>{`{${v}}`}</code>
                      {i < row.template_vars.length - 1 && ", "}
                    </span>
                  ))}
                </>
              )}
            </div>
          </div>

          {/* Preview */}
          <details className="rounded-xl border border-border bg-bg-subtle/40 p-3">
            <summary className="flex cursor-pointer items-center gap-1 text-[11px] uppercase tracking-wider text-fg-subtle">
              <ChevronDown className="h-3 w-3" /> Заводской текст (read-only)
            </summary>
            <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-fg-muted">
              {row.default_text_ru}
            </pre>
          </details>
        </div>

        <div className="flex items-center gap-2 border-t border-border p-4">
          {!row.is_code_registered && (
            <button
              type="button"
              onClick={() => {
                if (confirm(`Удалить «${row.title}»? Действие необратимо.`))
                  del.mutate();
              }}
              disabled={del.isPending}
              className="btn-ghost text-danger hover:text-danger"
              title="Удалить admin-created уведомление (код-owned нельзя)"
            >
              {del.isPending ? <Spinner /> : <Trash2 className="h-3.5 w-3.5" />}
            </button>
          )}
          <button
            type="button"
            onClick={() => testSend.mutate()}
            disabled={testSend.isPending}
            className="btn-secondary"
            title="Отправить текущий текст (в TEXTAREA) себе в Telegram — проверка HTML/emoji/переносов"
          >
            {testSend.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
            Тест в TG
          </button>
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary flex-1"
            disabled={save.isPending}
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="btn-primary flex-1"
          >
            {save.isPending ? <Spinner /> : <Check className="h-3.5 w-3.5" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

function StatMini({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "success" | "danger";
}) {
  const textCls =
    tone === "success"
      ? "text-success"
      : tone === "danger"
      ? "text-danger"
      : "text-fg";
  return (
    <div className="rounded-lg border border-border bg-bg-card p-2 text-center">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div className={`mt-0.5 text-base font-semibold tabular-nums ${textCls}`}>
        {fmtNum(value)}
      </div>
    </div>
  );
}
