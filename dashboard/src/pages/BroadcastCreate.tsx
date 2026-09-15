import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useIdempotencyKeys } from "@/hooks/useIdempotencyKeys";
import {
  ArrowLeft,
  ArrowRight,
  Image as ImageIcon,
  Film,
  Send,
  Users as UsersIcon,
  CheckCircle2,
  X,
  AlertCircle,
} from "lucide-react";
import {
  ApiError, endpoints,
  uploadBroadcastPhoto, uploadBroadcastAnimation,
} from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton, Segmented, StatusDot } from "@/components/ui/controls";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { SegmentWindowPicker, useSegmentCount } from "@/components/segments/SegmentWindowPicker";
import { defaultKeyOf, isSegmentKeyValid, splitSegmentKey } from "@/lib/segments";

type Step = 1 | 2 | 3 | 4;

/** Field caption above an input (replaces the old uppercase eyebrow). */
const FIELD_LABEL = "t-mute mb-1.5 text-[13px]";
/** A small chip that stays visible on a `bg-tile-3` row. */
const CHIP = "badge bg-tile-1 t-mute";
/** Selectable option row: shade marks the chosen one, no outline. */
const optionRow = (active: boolean) =>
  cn(
    "flex cursor-pointer rounded-row text-[14px] transition-colors",
    active ? "bg-tile-4 text-ink" : "bg-tile-3 hover:bg-tile-4",
  );

// Tag colours on v3 tokens — same set as TAG_COLOR_CLASSES in Broadcasts.tsx
// (backend _VALID_TAG_COLORS). «yellow» is the cream brand accent in v3.
const TAG_SWATCHES = [
  { key: "gray", cls: "bg-ink/10 text-body" },
  { key: "red", cls: "bg-danger/15 text-danger" },
  { key: "orange", cls: "bg-warning/15 text-warning" },
  { key: "yellow", cls: "bg-accent/15 text-accent" },
  { key: "green", cls: "bg-success/15 text-success" },
  { key: "blue", cls: "bg-info/15 text-info" },
  { key: "purple", cls: "bg-special/15 text-special" },
];

const BUTTON_OPTIONS = [
  { key: "buy", label: "🛒 Купить" },
  { key: "promo_buy", label: "🎁 Купить со скидкой (нужен %)" },
  { key: "promo_traffic", label: "📊 Купить ГБ со скидкой (нужен %)" },
  // gift_reveal — reveal-сценка (👀 → 2с → 🎁 → экран тарифов со
  // скидкой). Процент админ выбирает в отдельном пикере ниже (20/25/
  // 30/35/40), продолжительность 48ч зашита в коде callback'а.
  { key: "gift_reveal", label: "👀 Посмотреть подарок (48ч, % ниже)" },
  // Одноразовые «месячные» подарки: −30% на конкретный период,
  // все 4 тарифа (Basic / Plus / Combo Basic / Combo Plus). Клик
  // ведёт сразу к выбору тарифа + payment-method (без экрана выбора
  // периода — период уже задан кнопкой).
  { key: "gift_1m", label: "🎁 −30% на 1 месяц (все тарифы)" },
  { key: "gift_3m", label: "🎁 −30% на 3 месяца (все тарифы)" },
  // Открывает 2-шаговый flow (тариф → период). Скидка ТОЛЬКО на 365
  // дней, остальные периоды по прайсу. Скидка одноразовая через FSM
  // (не пишется в user_discounts) — реализация симметрична gift_3m.
  { key: "gift_1y_40", label: "🎁 1 год со скидкой 40%" },
  { key: "support", label: "💬 Поддержка" },
  { key: "channel", label: "📢 Канал" },
  { key: "referral", label: "👥 Пригласить друга" },
  { key: "bypass", label: "🌐 Включить обход" },
  { key: "buy_combo", label: "🏆 Купить Комбо" },
  { key: "happ_ios", label: "📲 Happ iOS" },
  { key: "happ_android", label: "📲 Happ Android" },
  { key: "web_client", label: "🌐 Веб-клиент" },
  // Для рассылок владельцам прокси (сегмент bought_proxy) —
  // ведёт на delivery-экран «🧩 Ваш Telegram-прокси готов».
  { key: "my_proxy", label: "🧩 Мой прокси" },
  // Персональный подарок Combo Basic 1 мес со скидкой (% и часы
  // — из полей discount_percent/discount_hours рассылки).
  { key: "gift_combo", label: "🎁 Забрать подарок (Combo Basic 1м, нужен %)" },
  // Получатель таппает → бот открывает экран «Подари другу скидку 30%».
  // Внутри — кнопка share с его личной refd-ссылкой. Друг по ссылке
  // получает 30%/24ч (lifetime-once). Без extra discount-параметров —
  // и %, и продолжительность зашиты в коде (см. start.py refd_-handler).
  { key: "share_discount", label: "🎁 Поделиться скидкой (друг = −30% / 24ч)" },
  // Beta-testing: клик записывает юзера в beta_applications
  // (UNIQUE tg_id+program), удаляет сообщение и шлёт подтверждение.
  // Список заявок — /dashboard/beta-applications.
  { key: "beta_apply", label: "🧪 Оставить заявку (VPN-Инноватор, бета-тест)" },
  // «🎁 Получить пробный ключ»: клик выдаёт +1 день подписки и +1 ГБ обхода
  // (один раз на рассылку). После выдачи — «Подарок активирован» + экран
  // подключения устройства. Новому юзеру создаётся профиль в панели.
  { key: "trial_key", label: "🎁 Получить пробный ключ (+1 день, +1 ГБ)" },
];

export function BroadcastCreate() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const cloneParam = searchParams.get("clone");
  const cloneId = cloneParam && /^\d+$/.test(cloneParam) ? Number(cloneParam) : null;
  const [step, setStep] = useState<Step>(1);

  // Form state
  const [title, setTitle] = useState("");
  const [tag, setTag] = useState("");
  const [tagColor, setTagColor] = useState<string>("gray");
  const [message, setMessage] = useState("");
  const [photoFileId, setPhotoFileId] = useState<string | null>(null);
  const [animationFileId, setAnimationFileId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadingAnimation, setUploadingAnimation] = useState(false);
  const [segment, setSegment] = useState<string>("");
  const [buttons, setButtons] = useState<string[]>([]);
  const [discountPercent, setDiscountPercent] = useState<number | "">("");
  const [discountHours, setDiscountHours] = useState<number | "">(24);
  // Пресеты для «Посмотреть подарок». Согласованы с backend
  // (broadcasts.py:_GIFT_REVEAL_PERCENT_CHOICES). Дефолт 20% — если
  // админ не выбрал явно, летит текущее значение.
  const GIFT_REVEAL_PERCENT_CHOICES = [20, 25, 30, 35, 40] as const;
  const [giftRevealPercent, setGiftRevealPercent] = useState<number>(20);

  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: endpoints.broadcastSegments,
  });

  // Clone flow: если пришли с ?clone=N, подтягиваем прошлую рассылку и
  // предзаполняем всю форму (title/message/photo/buttons/скидки).
  // Сегмент НЕ подтягиваем — админ выбирает сам, чтобы не разослать
  // клон на ту же аудиторию по ошибке. clonedOnceRef защищает от
  // повторной установки при hot-reload / изменениях query.
  const clonedOnceRef = useRef(false);
  const cloneSrc = useQuery({
    queryKey: ["broadcasts", "detail", cloneId],
    queryFn: () => endpoints.broadcastDetail(cloneId as number),
    enabled: cloneId != null,
  });
  useEffect(() => {
    if (!cloneSrc.data || clonedOnceRef.current) return;
    const src = cloneSrc.data as Record<string, unknown>;
    const asStr = (v: unknown, fallback = "") =>
      typeof v === "string" ? v : fallback;
    const asNum = (v: unknown): number | null =>
      typeof v === "number" ? v : typeof v === "string" && v ? Number(v) : null;
    setTitle(asStr(src.title));
    setMessage(asStr(src.message));
    setPhotoFileId(asStr(src.photo_file_id) || null);
    setAnimationFileId(asStr(src.animation_file_id) || null);
    setTag(asStr(src.tag) || "");
    setTagColor(asStr(src.tag_color) || "gray");
    if (Array.isArray(src.buttons)) {
      setButtons((src.buttons as unknown[]).map((x) => String(x)));
    }
    const dp = asNum(src.discount_percent);
    if (dp !== null && dp > 0) setDiscountPercent(dp);
    const dh = asNum(src.discount_hours);
    if (dh !== null && dh > 0) setDiscountHours(dh);
    const gr = asNum(src.gift_reveal_percent);
    if (gr !== null && gr > 0) setGiftRevealPercent(gr);
    clonedOnceRef.current = true;
    toast.info(`Клон рассылки #${cloneId} — измени и отправь`);
  }, [cloneSrc.data, cloneId]);

  const submitKeys = useIdempotencyKeys();
  const create = useMutation({
    mutationFn: (body: Parameters<typeof endpoints.broadcastCreate>[0]) =>
      endpoints.broadcastCreate(body, submitKeys.opts("create", body)),
    onSuccess: (data) => {
      submitKeys.settle("create");
      toast.success(
        `Рассылка #${data.broadcast_id} запущена на ${fmtNum(data.audience)} получателей`,
      );
      navigate(`/broadcasts`);
    },
    onError: (e: unknown) => {
      submitKeys.settle("create", e);
      toast.error((e as ApiError)?.detail ?? "Не удалось запустить рассылку");
    },
  });
  const createBody = (): Parameters<typeof endpoints.broadcastCreate>[0] => ({
        title,
        message,
        segment,
        photo_file_id: photoFileId ?? null,
        animation_file_id: animationFileId ?? null,
        buttons,
        discount_percent:
          typeof discountPercent === "number" ? discountPercent : null,
        discount_hours: typeof discountHours === "number" ? discountHours : null,
        gift_reveal_percent: buttons.includes("gift_reveal")
          ? giftRevealPercent
          : null,
        tag: tag.trim() || null,
        tag_color: tag.trim() ? tagColor : null,
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
        animation_file_id: animationFileId ?? null,
        buttons,
        discount_percent:
          typeof discountPercent === "number" ? discountPercent : null,
        discount_hours: typeof discountHours === "number" ? discountHours : null,
        gift_reveal_percent: buttons.includes("gift_reveal")
          ? giftRevealPercent
          : null,
        tag: tag.trim() || null,
        tag_color: tag.trim() ? tagColor : null,
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

  // `segment` is the full key: "paid_lapsed_any" or "paid_ended:6m".
  const segmentBase = splitSegmentKey(segment).base;
  const segSpec = useMemo(
    () => segments.data?.find((x) => x.key === segmentBase),
    [segments.data, segmentBase],
  );
  const segmentValid = isSegmentKeyValid(segment, segments.data);
  const live = useSegmentCount(segSpec?.parametric && segmentValid ? segment : null);
  const audience = segSpec ? (segSpec.parametric ? live.count : segSpec.count) : null;
  const segmentLabel = segSpec
    ? segSpec.parametric
      ? live.label ?? segSpec.label
      : segSpec.label
    : "";

  const canNext1 = title.trim().length > 0 && message.trim().length > 0;
  const canNext2 = segmentValid;
  const needsDiscountPercent =
    buttons.includes("promo_buy") || buttons.includes("promo_traffic");
  const canConfirm =
    canNext1 &&
    canNext2 &&
    (needsDiscountPercent ? typeof discountPercent === "number" : true);

  const onPickPhoto = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const { file_id } = await uploadBroadcastPhoto(file);
      setPhotoFileId(file_id);
      // Фото и GIF взаимно-эксклюзивны — сбросим противоположное.
      setAnimationFileId(null);
      toast.success("Фото загружено");
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось загрузить фото");
    } finally {
      setUploading(false);
    }
  };

  const onPickAnimation = async (file: File | undefined) => {
    if (!file) return;
    setUploadingAnimation(true);
    try {
      const { file_id } = await uploadBroadcastAnimation(file);
      setAnimationFileId(file_id);
      // GIF и фото взаимно-эксклюзивны — сбросим противоположное.
      setPhotoFileId(null);
      toast.success("GIF загружен");
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось загрузить GIF");
    } finally {
      setUploadingAnimation(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Новая рассылка"
        actions={
          <button
            type="button"
            onClick={() => navigate("/broadcasts")}
            className="btn-secondary"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> К списку
          </button>
        }
      />

      {step === 1 && (
        <StepCard current={step} title="Текст" subtitle="Заголовок виден только в админке. Сообщение — то, что увидит пользователь.">
          <label className="block">
            <div className={FIELD_LABEL}>Заголовок (внутренний)</div>
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Напр. «Скидка 30% на Plus / 02.06»"
              autoFocus
            />
          </label>

          <div>
            <div className={FIELD_LABEL}>Тег / метка (необязательно)</div>
            <div className="flex flex-wrap items-center gap-3">
              <input
                type="text"
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                maxLength={40}
                placeholder="летняя акция / реактивация / A-B тест"
                className="input min-w-[180px] flex-1"
                aria-label="Тег / метка"
              />
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Цвет метки">
                {TAG_SWATCHES.map((c) => {
                  const active = tagColor === c.key;
                  return (
                    <button
                      type="button"
                      key={c.key}
                      onClick={() => setTagColor(c.key)}
                      title={c.key}
                      aria-label={c.key}
                      aria-pressed={active}
                      className={cn(
                        "tap-target grid h-7 w-7 place-items-center rounded-full text-[12px] font-semibold transition-opacity",
                        c.cls,
                        active ? "opacity-100" : "opacity-60 hover:opacity-100",
                      )}
                    >
                      {active ? <CheckCircle2 className="h-3.5 w-3.5" /> : "●"}
                    </button>
                  );
                })}
              </div>
            </div>
            <p className="t-mute mt-1.5 text-[12px] leading-4">
              Метка отображается chip'ом рядом с заголовком в списке рассылок.
              Помогает группировать по кампании/цели. Пусто = без тега.
            </p>
          </div>
          <label className="block">
            <div className="mb-1.5 flex items-center justify-between gap-3 text-[13px]">
              <span className="t-mute">Сообщение (HTML)</span>
              <span
                className={cn(
                  "tabular text-[12px]",
                  photoFileId && message.length > 1024 ? "text-warning" : "t-mute",
                )}
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
              <p className="mt-2 rounded-row bg-warning/15 px-3 py-2 text-[13px] leading-5 text-warning">
                ⚠️ Caption у фото лимит 1024 символа. У тебя {message.length}.
                Массовая рассылка упадёт. Либо убери фото, либо сократи текст.
                «Тест на админе» автоматически разделит на 2 сообщения, чтобы
                ты увидел рендер blockquote/expandable.
              </p>
            )}
          </label>

          <div>
            <div className={FIELD_LABEL}>Медиа (фото или GIF · необязательно)</div>
            {/* Attached state — единый блок для photo или animation */}
            {(photoFileId || animationFileId) ? (
              <div className="flex min-h-[52px] items-center gap-3 rounded-row bg-tile-3 py-2 pl-4 pr-2 text-[14px]">
                {photoFileId ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                ) : (
                  <Film className="h-4 w-4 shrink-0 text-info" />
                )}
                <div className="flex-1 truncate">
                  {photoFileId ? "🖼 Фото прикреплено" : "🎬 GIF прикреплён"}
                </div>
                <IconButton
                  small
                  label="Убрать медиа"
                  className="bg-tile-1"
                  onClick={() => {
                    setPhotoFileId(null);
                    setAnimationFileId(null);
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </IconButton>
              </div>
            ) : (
              // Два параллельных выбора: фото ИЛИ GIF.
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="t-body flex min-h-[52px] cursor-pointer items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[14px] transition-colors hover:bg-tile-4">
                  {uploading ? <Spinner /> : <ImageIcon className="h-4 w-4" />}
                  <span className="flex-1">
                    {uploading ? "Загружаю..." : "🖼 Фото (≤10MB, jpg/png)"}
                  </span>
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    disabled={uploading || uploadingAnimation}
                    onChange={(e) => onPickPhoto(e.target.files?.[0])}
                  />
                </label>
                <label className="t-body flex min-h-[52px] cursor-pointer items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[14px] transition-colors hover:bg-tile-4">
                  {uploadingAnimation ? <Spinner /> : <Film className="h-4 w-4" />}
                  <span className="flex-1">
                    {uploadingAnimation ? "Загружаю..." : "🎬 GIF/MP4 (≤20MB)"}
                  </span>
                  <input
                    type="file"
                    accept="image/gif,video/mp4"
                    className="hidden"
                    disabled={uploading || uploadingAnimation}
                    onChange={(e) => onPickAnimation(e.target.files?.[0])}
                  />
                </label>
              </div>
            )}
            <p className="t-mute mt-1.5 text-[12px] leading-4">
              Фото и GIF взаимно-эксклюзивные — при выборе одного второе
              сбрасывается. При загрузке бот отправит копию файла в твой
              Telegram — это нужно, чтобы получить <code>file_id</code>.
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
        <StepCard current={step} title="Аудитория" subtitle="Выбери сегмент. Счётчик обновляется в реальном времени.">
          {segments.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Считаю аудиторию...">
              <div className="t-mute flex items-center gap-2 text-[13px]">
                <Spinner /> Считаю аудиторию...
              </div>
              <Skeleton className="h-[52px] w-full rounded-row" />
              <Skeleton className="h-[52px] w-full rounded-row" />
              <Skeleton className="h-[52px] w-full rounded-row" />
            </div>
          ) : segments.isError ? (
            <ErrorState
              className="bg-tile-3"
              error={segments.error}
              onRetry={() => segments.refetch()}
            />
          ) : (
            <div className="flex flex-col gap-5">
              {(() => {
                const groups = new Map<string, typeof segments.data>();
                for (const s of segments.data ?? []) {
                  const g = s.group || "Прочее";
                  if (!groups.has(g)) groups.set(g, []);
                  (groups.get(g) as NonNullable<typeof segments.data>).push(s);
                }
                return Array.from(groups.entries()).map(([groupName, items]) => (
                  <section key={groupName}>
                    <h3 className="t-mute mb-2 text-[13px] font-medium">{groupName}</h3>
                    <ul className="flex flex-col gap-2">
                      {(items ?? []).map((s) => {
                        const active = segmentBase === s.key;
                        return (
                          <li key={s.key} className="flex flex-col gap-2">
                            <label
                              className={cn(
                                optionRow(active),
                                "items-start justify-between gap-3 px-4 py-3",
                              )}
                            >
                              <div className="flex min-w-0 items-start gap-3">
                                <input
                                  type="radio"
                                  name="segment"
                                  value={s.key}
                                  checked={active}
                                  onChange={() => setSegment(defaultKeyOf(s))}
                                  className="mt-1 accent-accent"
                                />
                                <div className="min-w-0">
                                  <div className="font-medium">
                                    {s.label}
                                    {s.parametric && (
                                      <span className="t-mute font-normal"> · за период</span>
                                    )}
                                  </div>
                                  {s.description && (
                                    <div className="t-mute mt-0.5 text-[12px] leading-snug">
                                      {s.description}
                                    </div>
                                  )}
                                </div>
                              </div>
                              <span
                                className={cn("tabular shrink-0", active ? "badge-accent" : CHIP)}
                                title={s.parametric ? s.default_label : undefined}
                              >
                                <UsersIcon className="h-3 w-3" />{" "}
                                {fmtNum(active && s.parametric ? live.count : s.count)}
                              </span>
                            </label>
                            {active && s.parametric && (
                              <SegmentWindowPicker spec={s} value={segment} onChange={setSegment} />
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ));
              })()}
            </div>
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
          current={step}
          title="Кнопки"
          subtitle="Появятся под сообщением. Можно ничего не выбирать — рассылка уйдёт без CTA."
        >
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {BUTTON_OPTIONS.map((b) => {
              const checked = buttons.includes(b.key);
              return (
                <label
                  key={b.key}
                  className={cn(
                    optionRow(checked),
                    "min-h-[44px] items-center gap-3 px-3 py-2.5",
                  )}
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
                  <span>{b.label}</span>
                </label>
              );
            })}
          </div>

          {needsDiscountPercent && (
            <div className="rounded-row bg-tile-2 p-4">
              <h3 className="flex items-center gap-2 text-[15px] font-semibold">
                <StatusDot tone="warn" />
                Параметры скидки {buttons.includes("promo_traffic")
                  ? "(для «Купить ГБ со скидкой»)"
                  : "(для «Купить со скидкой»)"}
              </h3>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className="block">
                  <div className={FIELD_LABEL}>%</div>
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
                  <div className={FIELD_LABEL}>часов действия</div>
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

          {/* Percent picker для «👀 Посмотреть подарок». Показывается
              только если админ выбрал эту кнопку в списке выше.
              Продолжительность 48ч зашита в коде callback'а. */}
          {buttons.includes("gift_reveal") && (
            <div className="rounded-row bg-tile-2 p-4">
              <h3 className="text-[15px] font-semibold">
                👀 Посмотреть подарок — скидка после reveal
              </h3>
              <div className="t-body mt-1 text-[13px]">
                Действует <b className="font-semibold text-ink">48 часов</b> после клика. Выбери процент:
              </div>
              <div className="mt-3">
                <Segmented
                  label="Процент скидки подарка"
                  value={giftRevealPercent}
                  options={GIFT_REVEAL_PERCENT_CHOICES.map((p) => ({
                    value: p as number,
                    label: `${p} %`,
                  }))}
                  onChange={setGiftRevealPercent}
                />
              </div>
            </div>
          )}

          <Nav
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
            nextDisabled={
              needsDiscountPercent && typeof discountPercent !== "number"
            }
          />
        </StepCard>
      )}

      {step === 4 && (
        <StepCard
          current={step}
          title="Подтверждение"
          subtitle="Это уйдёт N юзерам прямо сейчас. Отменить нельзя."
        >
          <div className="rounded-row bg-tile-3 p-4">
            <div className="t-mute text-[13px]">Сегмент</div>
            <div className="mt-1 text-[15px] font-semibold">{segmentLabel}</div>
            <div className="mt-3 flex flex-wrap items-baseline gap-x-2">
              <span className="tabular track-metric text-[30px] font-semibold leading-9">
                {fmtNum(audience)}
              </span>
              <span className="t-mute inline-flex items-center gap-1 text-[13px]">
                <UsersIcon className="h-3.5 w-3.5" /> получателей
              </span>
            </div>
          </div>

          <div className="rounded-row bg-tile-3 p-4">
            <div className="t-mute mb-2 text-[13px]">Текст сообщения</div>
            <div
              className="whitespace-pre-wrap text-[14px] leading-relaxed"
              dangerouslySetInnerHTML={{ __html: sanitize(message) }}
            />
            {photoFileId && (
              <div className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-success">
                <ImageIcon className="h-3 w-3" /> С фото
              </div>
            )}
            {animationFileId && (
              <div className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-info">
                <Film className="h-3 w-3" /> С GIF
              </div>
            )}
            {buttons.length > 0 && (
              <div className="mt-3 flex flex-col gap-1.5">
                <div className="t-mute text-[12px]">Кнопки:</div>
                <div className="flex flex-wrap gap-1.5">
                  {buttons.map((b) => {
                    const label =
                      BUTTON_OPTIONS.find((x) => x.key === b)?.label ?? b;
                    return (
                      <span key={b} className="badge bg-tile-1 t-body">
                        {label}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {audience === 0 && (
            <div role="alert" className="flex items-center gap-2 rounded-row bg-danger/15 px-4 py-3 text-[14px] text-danger">
              <AlertCircle className="h-4 w-4 shrink-0" />
              Аудитория пустая — отправлять некому.
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
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
              onClick={() => create.mutate(createBody())}
              disabled={
                create.isPending ||
                testSelf.isPending ||
                !canConfirm ||
                audience === 0 ||
                audience === null
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
  current,
  title,
  subtitle,
  children,
}: {
  current: Step;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <Surface className="animate-slide-up md:p-6" label={title} aside={<Steps current={current} />}>
      {subtitle && (
        <p className="t-mute -mt-1 mb-5 max-w-[65ch] text-[13px] leading-5">{subtitle}</p>
      )}
      <div className="flex flex-col gap-5">{children}</div>
    </Surface>
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
    <ol className="hidden items-center gap-1.5 md:flex" aria-label="Шаги">
      {steps.map((s, i) => (
        <li
          key={s.n}
          className="flex items-center gap-1.5"
          aria-current={s.n === current ? "step" : undefined}
        >
          <span
            className={cn(
              "tabular grid h-6 w-6 place-items-center rounded-full text-[12px] font-semibold",
              s.n === current
                ? "bg-accent text-onaccent"
                : s.n < current
                ? "bg-tile-4 text-ink"
                : "t-mute bg-tile-3",
            )}
          >
            {s.n}
          </span>
          <span
            className={
              s.n === current ? "text-[12px] font-medium text-ink" : "t-mute text-[12px]"
            }
          >
            {s.label}
          </span>
          {i < steps.length - 1 && (
            <span className="mx-1 h-px w-4 bg-tile-4" aria-hidden="true" />
          )}
        </li>
      ))}
    </ol>
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
    <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
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
