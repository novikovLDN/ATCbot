import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Image as ImageIcon,
  Send,
  Users as UsersIcon,
  CheckCircle2,
  X,
  AlertCircle,
} from "lucide-react";
import { ApiError, endpoints, uploadBroadcastPhoto } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";

type Step = 1 | 2 | 3 | 4;

const BUTTON_OPTIONS = [
  { key: "buy", label: "🛒 Купить" },
  { key: "promo_buy", label: "🎁 Купить со скидкой (нужен %)" },
  { key: "support", label: "💬 Поддержка" },
  { key: "channel", label: "📢 Канал" },
  { key: "referral", label: "👥 Пригласить друга" },
  { key: "bypass", label: "🌐 Включить обход" },
  { key: "buy_combo", label: "🏆 Купить Комбо" },
  { key: "happ_ios", label: "📲 Happ iOS" },
  { key: "happ_android", label: "📲 Happ Android" },
  { key: "web_client", label: "🌐 Веб-клиент" },
];

export function BroadcastCreate() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(1);

  // Form state
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [photoFileId, setPhotoFileId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [segment, setSegment] = useState<string>("");
  const [buttons, setButtons] = useState<string[]>([]);
  const [discountPercent, setDiscountPercent] = useState<number | "">("");
  const [discountHours, setDiscountHours] = useState<number | "">(24);

  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: endpoints.broadcastSegments,
  });

  const create = useMutation({
    mutationFn: () =>
      endpoints.broadcastCreate({
        title,
        message,
        segment,
        photo_file_id: photoFileId ?? null,
        buttons,
        discount_percent:
          typeof discountPercent === "number" ? discountPercent : null,
        discount_hours: typeof discountHours === "number" ? discountHours : null,
      }),
    onSuccess: (data) => {
      toast.success(
        `Рассылка #${data.broadcast_id} запущена на ${fmtNum(data.audience)} получателей`,
      );
      navigate(`/broadcasts`);
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось запустить рассылку"),
  });

  // Тест на админе: те же поля, но сообщение уходит ТОЛЬКО админу.
  // Никаких записей в БД, ничего получателям. Нужен чтобы проверить
  // разметку, premium-эмодзи, фото и кнопки перед массовой отправкой.
  const testSelf = useMutation({
    mutationFn: () =>
      endpoints.broadcastTestSelf({
        title: title || "(тест)",
        message,
        segment: segment || "active_subscriptions",
        photo_file_id: photoFileId ?? null,
        buttons,
        discount_percent:
          typeof discountPercent === "number" ? discountPercent : null,
        discount_hours: typeof discountHours === "number" ? discountHours : null,
      }),
    onSuccess: (data) => {
      if (data.split) {
        toast.success(
          "Тест отправлен. Caption не влез — разбили на 2 сообщения (фото + текст). При массовой рассылке так же не влезет — сократи текст или убери фото.",
        );
      } else {
        toast.success("Тест отправлен — проверь свой чат");
      }
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить тест"),
  });

  const audience = useMemo(() => {
    if (!segments.data) return null;
    const s = segments.data.find((x) => x.key === segment);
    return s ? s.count : null;
  }, [segments.data, segment]);

  const canNext1 = title.trim().length > 0 && message.trim().length > 0;
  const canNext2 = segment.length > 0;
  const canConfirm =
    canNext1 &&
    canNext2 &&
    (buttons.includes("promo_buy") ? typeof discountPercent === "number" : true);

  const onPickPhoto = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const { file_id } = await uploadBroadcastPhoto(file);
      setPhotoFileId(file_id);
      toast.success("Фото загружено");
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось загрузить фото");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <button
            type="button"
            onClick={() => navigate("/broadcasts")}
            className="btn-ghost mb-2 -ml-2"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> К списку
          </button>
          <h1 className="text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Новая рассылка
          </h1>
        </div>
        <Steps current={step} />
      </header>

      {step === 1 && (
        <StepCard title="Текст" subtitle="Заголовок виден только в админке. Сообщение — то, что увидит пользователь.">
          <label className="block">
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Заголовок (внутренний)
            </div>
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Напр. «Скидка 30% на Plus / 02.06»"
              autoFocus
            />
          </label>
          <label className="block">
            <div className="mb-1.5 flex items-center justify-between text-xs font-medium uppercase tracking-wider text-fg-subtle">
              <span>Сообщение (HTML)</span>
              <span
                className={
                  "font-normal normal-case " +
                  (photoFileId && message.length > 1024
                    ? "text-amber-500"
                    : "text-fg-subtle")
                }
              >
                {message.length} / {photoFileId ? 1024 : 4000}
                {photoFileId ? " (caption фото)" : ""}
              </span>
            </div>
            <textarea
              className="input min-h-[200px] resize-y font-sans leading-relaxed"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              maxLength={4000}
              placeholder="Поддерживается HTML: <b>жирный</b>, <i>курсив</i>, <a href=...>ссылки</a>, <blockquote>цитаты</blockquote>, <blockquote expandable>скрытая</blockquote>"
            />
            {photoFileId && message.length > 1024 && (
              <p className="mt-1.5 text-xs text-amber-500">
                ⚠️ Caption у фото лимит 1024 символа. У тебя {message.length}.
                Массовая рассылка упадёт. Либо убери фото, либо сократи текст.
                «Тест на админе» автоматически разделит на 2 сообщения, чтобы
                ты увидел рендер blockquote/expandable.
              </p>
            )}
          </label>

          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Фото (необязательно)
            </div>
            {photoFileId ? (
              <div className="flex items-center gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-sm">
                <CheckCircle2 className="h-4 w-4 text-success" />
                <div className="flex-1 truncate text-fg">Фото прикреплено</div>
                <button
                  type="button"
                  onClick={() => setPhotoFileId(null)}
                  className="btn-ghost"
                  aria-label="Убрать фото"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-border bg-bg-subtle/40 px-4 py-4 text-sm text-fg-muted transition hover:border-fg-subtle hover:bg-bg-elevated/60">
                {uploading ? <Spinner /> : <ImageIcon className="h-4 w-4" />}
                <span className="flex-1">
                  {uploading
                    ? "Загружаю в Telegram..."
                    : "Выбрать файл (≤10MB, jpg/png)"}
                </span>
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  disabled={uploading}
                  onChange={(e) => onPickPhoto(e.target.files?.[0])}
                />
              </label>
            )}
            <p className="mt-1.5 text-[11px] text-fg-subtle">
              При загрузке бот отправит копию фото в твой Telegram — это
              нужно, чтобы получить <code>file_id</code> для рассылки. Так же
              устроен встроенный конструктор.
            </p>
          </div>

          <Nav
            onBack={() => navigate("/broadcasts")}
            onNext={() => setStep(2)}
            nextDisabled={!canNext1}
          />
        </StepCard>
      )}

      {step === 2 && (
        <StepCard title="Аудитория" subtitle="Выбери сегмент. Счётчик обновляется в реальном времени.">
          {segments.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Spinner /> Считаю аудиторию...
            </div>
          ) : segments.isError ? (
            <div className="rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              Не удалось загрузить сегменты.
            </div>
          ) : (
            <ul className="space-y-1.5">
              {(segments.data ?? []).map((s) => (
                <li key={s.key}>
                  <label
                    className={
                      segment === s.key
                        ? "flex cursor-pointer items-center justify-between rounded-xl border border-accent/40 bg-accent/10 px-4 py-3 text-sm transition"
                        : "flex cursor-pointer items-center justify-between rounded-xl border border-border bg-bg-card px-4 py-3 text-sm transition hover:border-fg-subtle hover:bg-bg-elevated/60"
                    }
                  >
                    <div className="flex items-center gap-3">
                      <input
                        type="radio"
                        name="segment"
                        value={s.key}
                        checked={segment === s.key}
                        onChange={() => setSegment(s.key)}
                        className="accent-accent"
                      />
                      <span className="font-medium text-fg">{s.label}</span>
                    </div>
                    <span className="badge-muted">
                      <UsersIcon className="h-3 w-3" /> {fmtNum(s.count)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <Nav
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
            nextDisabled={!canNext2}
          />
        </StepCard>
      )}

      {step === 3 && (
        <StepCard
          title="Кнопки"
          subtitle="Появятся под сообщением. Можно ничего не выбирать — рассылка уйдёт без CTA."
        >
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {BUTTON_OPTIONS.map((b) => {
              const checked = buttons.includes(b.key);
              return (
                <label
                  key={b.key}
                  className={
                    checked
                      ? "flex cursor-pointer items-center gap-3 rounded-xl border border-accent/40 bg-accent/10 px-3 py-2.5 text-sm transition"
                      : "flex cursor-pointer items-center gap-3 rounded-xl border border-border bg-bg-card px-3 py-2.5 text-sm transition hover:border-fg-subtle"
                  }
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      if (e.target.checked) setButtons([...buttons, b.key]);
                      else setButtons(buttons.filter((x) => x !== b.key));
                    }}
                    className="accent-accent"
                  />
                  <span className="text-fg">{b.label}</span>
                </label>
              );
            })}
          </div>

          {buttons.includes("promo_buy") && (
            <div className="rounded-xl border border-warning/30 bg-warning/10 p-4">
              <div className="text-xs font-medium uppercase tracking-wider text-warning">
                Параметры скидки (для «Купить со скидкой»)
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="block">
                  <div className="mb-1 text-xs text-fg-subtle">%</div>
                  <input
                    className="input"
                    type="number"
                    min={1}
                    max={100}
                    value={discountPercent}
                    onChange={(e) =>
                      setDiscountPercent(
                        e.target.value === "" ? "" : Number(e.target.value),
                      )
                    }
                    placeholder="напр. 30"
                  />
                </label>
                <label className="block">
                  <div className="mb-1 text-xs text-fg-subtle">часов действия</div>
                  <input
                    className="input"
                    type="number"
                    min={1}
                    value={discountHours}
                    onChange={(e) =>
                      setDiscountHours(
                        e.target.value === "" ? "" : Number(e.target.value),
                      )
                    }
                    placeholder="24"
                  />
                </label>
              </div>
            </div>
          )}

          <Nav
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
            nextDisabled={
              buttons.includes("promo_buy") && typeof discountPercent !== "number"
            }
          />
        </StepCard>
      )}

      {step === 4 && (
        <StepCard
          title="Подтверждение"
          subtitle="Это уйдёт N юзерам прямо сейчас. Отменить нельзя."
        >
          <div className="rounded-xl border border-border bg-bg-subtle/40 p-4">
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Сегмент
            </div>
            <div className="mt-1 text-base font-semibold text-fg">
              {segments.data?.find((x) => x.key === segment)?.label}
            </div>
            <div className="mt-1 text-sm text-fg-muted">
              <UsersIcon className="mr-1 inline h-3.5 w-3.5" />
              <b>{fmtNum(audience)}</b> получателей
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Текст сообщения
            </div>
            <div
              className="whitespace-pre-wrap text-sm leading-relaxed text-fg"
              dangerouslySetInnerHTML={{ __html: sanitize(message) }}
            />
            {photoFileId && (
              <div className="mt-3 inline-flex items-center gap-1.5 text-xs text-success">
                <ImageIcon className="h-3 w-3" /> С фото
              </div>
            )}
            {buttons.length > 0 && (
              <div className="mt-3 space-y-1.5">
                <div className="text-[11px] uppercase tracking-wider text-fg-subtle">
                  Кнопки:
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {buttons.map((b) => {
                    const label =
                      BUTTON_OPTIONS.find((x) => x.key === b)?.label ?? b;
                    return (
                      <span key={b} className="badge-muted">
                        {label}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {audience === 0 && (
            <div className="flex items-center gap-2 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              <AlertCircle className="h-4 w-4" />
              Аудитория пустая — отправлять некому.
            </div>
          )}

          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => setStep(3)}
              className="btn-secondary"
              disabled={create.isPending || testSelf.isPending}
            >
              <ArrowLeft className="h-3.5 w-3.5" /> Назад
            </button>
            <button
              type="button"
              onClick={() => testSelf.mutate()}
              disabled={
                create.isPending ||
                testSelf.isPending ||
                message.trim().length === 0
              }
              className="btn-secondary"
              title="Отправит это сообщение только тебе — проверь рендер и кнопки перед массовой рассылкой"
            >
              {testSelf.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
              Тест на админе
            </button>
            <button
              type="button"
              onClick={() => create.mutate()}
              disabled={
                create.isPending ||
                testSelf.isPending ||
                !canConfirm ||
                audience === 0
              }
              className="btn-primary"
            >
              {create.isPending ? <Spinner /> : <Send className="h-3.5 w-3.5" />}
              Запустить рассылку
            </button>
          </div>
        </StepCard>
      )}
    </div>
  );
}

function StepCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card space-y-4 p-5 md:p-6 animate-slide-up">
      <div>
        <h2 className="text-lg font-semibold text-fg">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

function Steps({ current }: { current: Step }) {
  const steps: { n: Step; label: string }[] = [
    { n: 1, label: "Текст" },
    { n: 2, label: "Аудитория" },
    { n: 3, label: "Кнопки" },
    { n: 4, label: "Запуск" },
  ];
  return (
    <div className="hidden items-center gap-1.5 md:flex">
      {steps.map((s, i) => (
        <div key={s.n} className="flex items-center gap-1.5">
          <div
            className={
              s.n === current
                ? "grid h-6 w-6 place-items-center rounded-full bg-accent text-[11px] font-semibold text-white"
                : s.n < current
                ? "grid h-6 w-6 place-items-center rounded-full bg-success/15 text-[11px] font-semibold text-success ring-1 ring-success/30"
                : "grid h-6 w-6 place-items-center rounded-full bg-bg-elevated text-[11px] font-semibold text-fg-subtle ring-1 ring-border"
            }
          >
            {s.n}
          </div>
          <span
            className={
              s.n === current
                ? "text-xs font-medium text-fg"
                : "text-xs text-fg-subtle"
            }
          >
            {s.label}
          </span>
          {i < steps.length - 1 && (
            <div className="mx-1 h-px w-4 bg-border" />
          )}
        </div>
      ))}
    </div>
  );
}

function Nav({
  onBack,
  onNext,
  nextDisabled,
  nextLabel = "Дальше",
}: {
  onBack: () => void;
  onNext: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
}) {
  return (
    <div className="flex items-center justify-between pt-2">
      <button type="button" onClick={onBack} className="btn-secondary">
        <ArrowLeft className="h-3.5 w-3.5" /> Назад
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={nextDisabled}
        className="btn-primary"
      >
        {nextLabel} <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function sanitize(html: string): string {
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/on\w+="[^"]*"/gi, "")
    .replace(/javascript:/gi, "");
}
