import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
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
import { cn } from "@/lib/cn";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { IconButton, StatusDot, type Tone } from "@/components/ui/controls";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { SegmentSelect } from "@/components/segments/SegmentSelect";

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

const FIELD_LABEL = "mb-1.5 block text-[13px] font-medium t-body";
const FIELD_HELP = "t-mute mt-1.5 text-[12px] leading-5";
const CODE_CHIP = "rounded-full bg-tile-3 px-2 py-0.5 font-mono text-[12px]";

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
    <>
      <PageHeader
        title="Автоуведомления"
        sub={
          <>
            Все автоматические сообщения, которые бот шлёт пользователям по расписанию (reminders, приветствия,
            оффер-триггеры). Здесь можно править текст, отключать и менять окно отправки — <b>без релиза</b>.
          </>
        }
        actions={
          <>
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
          </>
        }
      />

      <Bento>
        {/* KPI-сводка */}
        <KpiTile
          className="sm:col-span-2 xl:col-span-4"
          size="sm"
          label="Активных"
          loading={list.isLoading}
          value={<ToneValue tone="ok" label="Активны">{fmtNum(totalEnabled)}</ToneValue>}
        />
        <KpiTile
          className="sm:col-span-2 xl:col-span-4"
          size="sm"
          variant="raised"
          label="Отключено"
          loading={list.isLoading}
          value={<ToneValue tone="err" label="Отключены">{fmtNum(totalDisabled)}</ToneValue>}
        />
        <KpiTile
          className="sm:col-span-2 xl:col-span-4"
          size="sm"
          variant="steel"
          label="С кастомным текстом"
          loading={list.isLoading}
          value={fmtNum(totalCustom)}
        />

        {/* Пояснение как читать колонки */}
        <Surface className="sm:col-span-6 xl:col-span-12" variant="raised" label="Подсказки">
          <ul className="grid grid-cols-1 gap-2 text-[13px] leading-5 md:grid-cols-3">
            <li className="rounded-row bg-tile-3 p-3">
              <span className="font-semibold">Тумблер</span>
              <span className="t-body">
                {" "}
                — включить/отключить уведомление (немедленное действие, следующий тик планировщика уже пропустит).
              </span>
            </li>
            <li className="rounded-row bg-tile-3 p-3">
              <span className="font-semibold">Изменить</span>
              <span className="t-body">
                {" "}
                — правка текста и окна отправки (для reminder'ов — сколько часов до истечения, ±допуск).
              </span>
            </li>
            <li className="rounded-row bg-tile-3 p-3">
              <span className="font-semibold">Сброс</span>
              <span className="t-body"> — вернуть заводской текст из кода.</span>
            </li>
          </ul>
        </Surface>

        {list.isLoading ? (
          <Surface className="sm:col-span-6 xl:col-span-12" aria-label="Загружаю список…">
            <div className="flex flex-col gap-2" role="status" aria-label="Загружаю список…">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 rounded-row" />
              ))}
            </div>
          </Surface>
        ) : list.isError ? (
          <ErrorState className="sm:col-span-6 xl:col-span-12" error={list.error} onRetry={() => list.refetch()} />
        ) : (
          catOrder.map((cat) => {
            const rows = grouped.get(cat);
            if (!rows || rows.length === 0) return null;
            const active = rows.filter((r) => r.is_enabled).length;
            return (
              <CategoryGroup
                key={cat}
                title={CATEGORY_LABEL[cat] ?? cat}
                subtitle={`${rows.length} уведомлений · активных: ${active}`}
                defaultOpen={cat === "trial"}
                remember={`autonotif-cat-${cat}`}
              >
                {rows.map((n) => (
                  <NotificationRow key={n.key} row={n} onEdit={() => setEditing(n)} />
                ))}
              </CategoryGroup>
            );
          })
        )}
      </Bento>

      {editing && <EditModal row={editing} onClose={() => setEditing(null)} />}
      {showCreate && <CreateModal onClose={() => setShowCreate(false)} />}
    </>
  );
}

function ToneValue({ tone, label, children }: { tone: Tone; label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-3">
      <StatusDot tone={tone} label={label} />
      {children}
    </span>
  );
}

/**
 * Раскрывающаяся категория. Состояние открытия хранится в localStorage
 * под тем же ключом, что и у общего Collapsible (`collapsible:<remember>`),
 * так что сохранённые раньше состояния продолжают работать.
 */
function CategoryGroup({
  title,
  subtitle,
  defaultOpen,
  remember,
  children,
}: {
  title: string;
  subtitle: string;
  defaultOpen: boolean;
  remember: string;
  children: ReactNode;
}) {
  const storageKey = `collapsible:${remember}`;
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(storageKey);
      if (v === "1") return true;
      if (v === "0") return false;
    } catch {
      /* localStorage disabled — fall through */
    }
    return defaultOpen;
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [open, storageKey]);

  return (
    <Surface className="sm:col-span-6 xl:col-span-12">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="no-tap-highlight -m-2 flex min-h-[44px] w-[calc(100%+1rem)] items-center justify-between gap-3 rounded-row p-2 text-left transition-colors hover:bg-tile-3"
        aria-expanded={open}
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold">{title}</span>
          <span className="t-mute mt-0.5 block text-[12px]">{subtitle}</span>
        </span>
        <span className="icon-btn icon-btn-sm bg-tile-3" aria-hidden="true">
          <ChevronDown className={cn("h-4 w-4 transition-transform duration-300 ease-out", open && "rotate-180")} />
        </span>
      </button>
      <div className="collapsible" data-open={open ? "true" : "false"}>
        <div className="collapsible-inner">
          <div className="flex flex-col gap-2 pt-4">{children}</div>
        </div>
      </div>
    </Surface>
  );
}

function ModalHeader({ title, sub, onClose }: { title: ReactNode; sub?: ReactNode; onClose: () => void }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="text-[18px] font-semibold leading-6">{title}</h3>
        {sub && <div className="t-mute mt-1 text-[13px] leading-5">{sub}</div>}
      </div>
      <IconButton label="Закрыть" onClick={onClose} className="bg-tile-3 hover:bg-tile-4">
        <X className="h-4 w-4" />
      </IconButton>
    </div>
  );
}

function Callout({ children }: { children: ReactNode }) {
  return (
    <div className="t-body flex gap-2 rounded-row bg-tile-3 p-3 text-[13px] leading-5">
      <Info className="t-mute mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">{children}</div>
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
      <div className="tile w-full max-w-2xl p-5" role="dialog" aria-modal="true" aria-label="Новое уведомление">
        <ModalHeader
          title="Новое уведомление"
          sub="Создастся редактируемое admin-notification без code-триггера. Отправка — только вручную через «Тест в TG»."
          onClose={onClose}
        />

        <div className="flex flex-col gap-4">
          <Callout>
            <b>Как использовать:</b> admin-notification — заготовка текста для будущих кампаний или пробных
            отправок. Bot не шлёт их автоматически (нет code-триггера), но их можно править, отправлять себе
            тестом (кнопка «Тест в TG») и использовать через API из своих скриптов.
          </Callout>

          <label className="block">
            <span className={FIELD_LABEL}>Ключ (уникальный)</span>
            <input
              type="text"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="admin.summer_sale"
              className="input font-mono text-[13px]"
              autoFocus
            />
            <span className={cn(FIELD_HELP, "block")}>
              Формат: <code>namespace.name</code> · a-z, 0-9, _ · например, <code>admin.welcome_pro</code>.
              Namespace <code>admin.</code> рекомендуется, чтобы отличать от зашитых в коде.
            </span>
          </label>

          <label className="block">
            <span className={FIELD_LABEL}>Заголовок (что видит админ)</span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Летняя акция — welcome"
              className="input"
            />
          </label>

          <label className="block">
            <span className={FIELD_LABEL}>Описание (опционально)</span>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Для когорты новых юзеров, летний оффер"
              className="input"
            />
          </label>

          <label className="block">
            <span className={FIELD_LABEL}>Категория</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="input">
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
            <span className={FIELD_LABEL}>Текст сообщения (HTML)</span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              placeholder="🎁 <b>Летняя акция!</b>&#10;&#10;Скидка 20% на любой тариф до конца недели."
              className="input font-mono text-[12px] leading-relaxed"
            />
            <span className={cn(FIELD_HELP, "block")}>
              Поддерживаются HTML-теги Telegram: <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>,{" "}
              <code>&lt;code&gt;</code>, <code>&lt;blockquote&gt;</code>. Эмодзи и <code>&lt;tg-emoji&gt;</code> —
              тоже.
            </span>
          </label>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button type="button" onClick={onClose} className="btn-secondary flex-1" disabled={create.isPending}>
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
    <div className="flex items-start gap-3 rounded-row bg-tile-3 p-3">
      <button
        type="button"
        onClick={() => toggle.mutate()}
        disabled={toggle.isPending}
        title={row.is_enabled ? "Отключить" : "Включить"}
        aria-label={row.is_enabled ? "Отключить" : "Включить"}
        aria-pressed={row.is_enabled}
        className={cn(
          "tap-target mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full transition-colors disabled:opacity-50",
          row.is_enabled
            ? "bg-success/15 text-success hover:bg-success/25"
            : "t-mute bg-tile-1 hover:bg-tile-4",
        )}
      >
        <Power className="h-4 w-4" />
      </button>
      <div className={cn("min-w-0 flex-1", !row.is_enabled && "opacity-70")}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-semibold">{row.title}</span>
          {row.has_custom_text && <span className="badge-info">CUSTOM</span>}
          {!row.is_enabled && <span className="badge-danger">OFF</span>}
        </div>
        <div className="t-mute mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
          <code className="rounded-full bg-tile-1 px-2 py-0.5 font-mono text-[12px]">{row.key}</code>
          {triggerLabel && (
            <span className="tabular inline-flex items-center gap-1">
              <Timer className="h-3 w-3" aria-hidden="true" /> {triggerLabel}
            </span>
          )}
          {row.updated_at && <span>· изменено {fmtDate(row.updated_at)}</span>}
        </div>
        {row.description && <div className="t-body mt-1.5 text-[13px] leading-5">{row.description}</div>}
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="btn-secondary shrink-0 self-center bg-tile-1 hover:bg-tile-4"
        aria-label="Изменить"
      >
        <Edit3 className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Изменить</span>
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
      <div className="tile w-full max-w-2xl p-5" role="dialog" aria-modal="true" aria-label={row.title}>
        <ModalHeader title={row.title} sub={<code className={CODE_CHIP}>{row.key}</code>} onClose={onClose} />

        <div className="flex flex-col gap-5">
          {row.description && <Callout>{row.description}</Callout>}

          {/* Stats за 7 дней */}
          <div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatMini label="Отправлено" value={stats.data?.sent ?? 0} tone="ok" />
              <StatMini label="Ошибки" value={stats.data?.failed ?? 0} tone="err" />
              <StatMini label="Заблокировали" value={stats.data?.blocked ?? 0} tone="err" />
              <StatMini label="Пропущено" value={stats.data?.skipped ?? 0} />
            </div>
            <p className={FIELD_HELP}>
              Статистика за последние 7 дней. «Пропущено» — юзеры для которых это уведомление было отключено на
              момент срабатывания триггера.
            </p>
          </div>

          {/* Trigger config (для reminder-типа) */}
          {isReminderConfig && (
            <section className="flex flex-col gap-3">
              <h4 className="text-[15px] font-semibold">Окно отправки</h4>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className={FIELD_LABEL}>За сколько часов до истечения</span>
                  <input
                    type="number"
                    value={beforeH}
                    onChange={(e) => setBeforeH(e.target.value)}
                    min={0.1}
                    max={720}
                    step={0.5}
                    className="input tabular"
                  />
                </label>
                <label className="block">
                  <span className={FIELD_LABEL}>Допуск (±часов)</span>
                  <input
                    type="number"
                    value={tolH}
                    onChange={(e) => setTolH(e.target.value)}
                    min={0}
                    max={48}
                    step={0.5}
                    className="input tabular"
                  />
                </label>
              </div>
              <p className="t-mute text-[12px] leading-5">
                Планировщик проверяет юзеров раз в минуту. Меньший допуск — более точное окно, но выше риск
                пропустить (если worker опоздает). 1ч по умолчанию — надёжный баланс.
              </p>

              <div className="block">
                <span className={FIELD_LABEL}>Дополнительный фильтр аудитории (опционально)</span>
                <SegmentSelect
                  segments={segments.data}
                  loading={segments.isLoading}
                  value={segmentFilter}
                  onChange={setSegmentFilter}
                  emptyLabel="— Без фильтра (шлём всем кто попал в окно) —"
                  ariaLabel="Дополнительный фильтр аудитории"
                />
                <span className={cn(FIELD_HELP, "block")}>
                  Если задан — reminder уйдёт только тем, кто ЕЩЁ и входит в этот сегмент на момент срабатывания
                  триггера. Полезно, например, для «7д до конца, но только тем, кто никогда не покупал» — узкий
                  таргет. Сегмент «за период» считается от момента срабатывания.
                </span>
              </div>
            </section>
          )}

          {/* Text editor */}
          <section>
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-[15px] font-semibold">Текст сообщения (HTML)</h4>
              <button
                type="button"
                onClick={() => reset.mutate()}
                disabled={reset.isPending || !row.has_custom_text}
                className="btn-ghost px-3 text-[13px]"
                title="Восстановить заводской текст"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Сброс к дефолту
              </button>
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={10}
              aria-label="Текст сообщения (HTML)"
              className="input font-mono text-[12px] leading-relaxed"
            />
            <p className={FIELD_HELP}>
              Поддерживаются HTML-теги Telegram: <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>,{" "}
              <code>&lt;code&gt;</code>, <code>&lt;blockquote&gt;</code>, <code>&lt;a href&gt;</code>. Эмодзи и{" "}
              <code>&lt;tg-emoji&gt;</code> — тоже.
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
            </p>
          </section>

          {/* Preview */}
          <details className="group rounded-row bg-tile-3">
            <summary className="t-body flex min-h-[44px] cursor-pointer list-none items-center [&::-webkit-details-marker]:hidden gap-1.5 px-3 text-[13px] font-medium">
              <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" aria-hidden="true" />
              Заводской текст (read-only)
            </summary>
            <pre className="t-body whitespace-pre-wrap px-3 pb-3 font-mono text-[12px] leading-relaxed">
              {row.default_text_ru}
            </pre>
          </details>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {!row.is_code_registered && (
            <button
              type="button"
              onClick={() => {
                if (confirm(`Удалить «${row.title}»? Действие необратимо.`))
                  del.mutate();
              }}
              disabled={del.isPending}
              className="btn-danger"
              title="Удалить admin-created уведомление (код-owned нельзя)"
              aria-label="Удалить уведомление"
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
          <button type="button" onClick={onClose} className="btn-secondary flex-1" disabled={save.isPending}>
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
  tone?: Tone;
}) {
  return (
    <div className="rounded-row bg-tile-3 p-3">
      <div className="t-mute flex items-center gap-1.5 text-[12px]">
        {tone && <StatusDot tone={tone} />}
        {label}
      </div>
      <div className="tabular mt-1 text-[22px] font-semibold leading-7">{fmtNum(value)}</div>
    </div>
  );
}
