import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Film, Image as ImageIcon, X } from "lucide-react";

import {
  ApiError,
  endpoints,
  uploadBroadcastAnimation,
  uploadBroadcastPhoto,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, Card, CardBody, CardHeader, Input, Spinner } from "@/components/ui";
import { ButtonPicker, type ButtonState } from "@/components/broadcasts/ButtonPicker";
import { NEEDS_DISCOUNT } from "@/components/broadcasts/buttonCatalog";
import { SegmentPicker } from "@/components/broadcasts/SegmentPicker";
import { SendReview } from "@/components/broadcasts/SendReview";
import { segmentCount, segmentLabel, useSegments } from "@/components/broadcasts/useSegments";

/**
 * «Рассылки» → вкладка «Новая рассылка».
 *
 * ЧЕТЫРЕ ШАГА: текст → кому → кнопки → проверка и отправка. Порядок не
 * произвольный: аудитория выбирается раньше кнопок, потому что от неё
 * зависит, уместна ли скидка, и позже текста, потому что текст пишут
 * первым.
 *
 * ЗДЕСЬ ТОЛЬКО СОСТОЯНИЕ ФОРМЫ И ПЕРЕХОДЫ. Выбор сегмента, выбор
 * кнопок и экран подтверждения — отдельные компоненты в
 * `components/broadcasts`; вся защита от случайной отправки живёт в
 * `SendReview`.
 *
 * ПОВТОР РАССЫЛКИ (?clone=N) ПОДТЯГИВАЕТ ВСЁ, КРОМЕ АУДИТОРИИ.
 * Сознательно: повторяют обычно удачный текст, но на другой сегмент, а
 * унаследованный молча сегмент — это способ разослать то же самое тем же
 * людям второй раз.
 */

type Step = 1 | 2 | 3 | 4;

const STEPS: Array<{ n: Step; label: string }> = [
  { n: 1, label: "Текст" },
  { n: 2, label: "Кому" },
  { n: 3, label: "Кнопки" },
  { n: 4, label: "Проверка" },
];

/** Лимит подписи под фото в Telegram. Больше — сообщение не уйдёт. */
const CAPTION_LIMIT = 1024;
const TEXT_LIMIT = 4000;

export function BroadcastCreate() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const cloneParam = searchParams.get("clone");
  const cloneId = cloneParam && /^\d+$/.test(cloneParam) ? Number(cloneParam) : null;

  const [step, setStep] = useState<Step>(1);
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [photoFileId, setPhotoFileId] = useState<string | null>(null);
  const [animationFileId, setAnimationFileId] = useState<string | null>(null);
  const [uploading, setUploading] = useState<"photo" | "animation" | null>(null);
  const [segment, setSegment] = useState("");
  const [testedAt, setTestedAt] = useState<Date | null>(null);
  const [btn, setBtn] = useState<ButtonState>({
    buttons: [],
    discountPercent: "",
    discountHours: 24,
    giftRevealPercent: 20,
  });

  const segments = useSegments();

  // ─ Повтор прошлой рассылки ─────────────────────────────────────────
  const clonedRef = useRef(false);
  const cloneSrc = useQuery({
    queryKey: ["broadcasts", "detail", cloneId],
    queryFn: () => endpoints.broadcastDetail(cloneId as number),
    enabled: cloneId != null,
  });

  useEffect(() => {
    if (!cloneSrc.data || clonedRef.current) return;
    const src = cloneSrc.data as Record<string, unknown>;
    const str = (v: unknown) => (typeof v === "string" ? v : "");
    const num = (v: unknown): number | null =>
      typeof v === "number" ? v : typeof v === "string" && v ? Number(v) : null;

    setTitle(str(src.title));
    setMessage(str(src.message));
    setPhotoFileId(str(src.photo_file_id) || null);
    setAnimationFileId(str(src.animation_file_id) || null);

    const dp = num(src.discount_percent);
    const dh = num(src.discount_hours);
    const gr = num(src.gift_reveal_percent);
    setBtn({
      buttons: Array.isArray(src.buttons) ? src.buttons.map(String) : [],
      discountPercent: dp != null && dp > 0 ? dp : "",
      discountHours: dh != null && dh > 0 ? dh : 24,
      giftRevealPercent: gr != null && gr > 0 ? gr : 20,
    });

    clonedRef.current = true;
    toast.info(`Взято из рассылки №${cloneId}. Аудиторию выберите заново`);
  }, [cloneSrc.data, cloneId]);

  // ─ Производные ─────────────────────────────────────────────────────
  const audience = useMemo(
    () => (segment ? segmentCount(segments.data, segment) : null),
    [segments.data, segment],
  );
  const segmentName = segmentLabel(segments.data, segment);
  const captionOverflow = Boolean(photoFileId) && message.length > CAPTION_LIMIT;
  const needsDiscount = btn.buttons.some((b) => NEEDS_DISCOUNT.includes(b));
  const discountReady = !needsDiscount || typeof btn.discountPercent === "number";

  const payload = () => ({
    title,
    message,
    segment,
    photo_file_id: photoFileId,
    animation_file_id: animationFileId,
    buttons: btn.buttons,
    discount_percent: typeof btn.discountPercent === "number" ? btn.discountPercent : null,
    discount_hours: typeof btn.discountHours === "number" ? btn.discountHours : null,
    gift_reveal_percent: btn.buttons.includes("gift_reveal")
      ? btn.giftRevealPercent
      : null,
  });

  // ─ Мутации ─────────────────────────────────────────────────────────
  const send = useMutation({
    mutationFn: () => endpoints.broadcastCreate(payload()),
    onSuccess: (data) => {
      toast.success(
        `Рассылка №${data.broadcast_id} пошла на ${fmtNum(data.audience)} получателей`,
      );
      // Возвращаемся сразу на её карточку: там виден живой ход отправки.
      navigate(`/broadcasts?id=${data.broadcast_id}`);
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось запустить рассылку"),
  });

  const test = useMutation({
    mutationFn: () =>
      endpoints.broadcastTestSelf({
        ...payload(),
        title: title || "(проверка)",
        // Сегмент серверу нужен для валидации, но на проверке он не
        // используется: сообщение уходит только админу.
        segment: segment || "active_subscriptions",
      }),
    onSuccess: (data) => {
      setTestedAt(new Date());
      if (data.split) {
        toast.info(
          "Пришло двумя сообщениями: подпись не влезла под фото. При массовой рассылке так не выйдет — сократите текст или уберите фото",
        );
      } else {
        toast.success("Отправлено вам — посмотрите свой чат");
      }
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить проверку"),
  });

  // ─ Загрузка медиа ──────────────────────────────────────────────────
  const pickMedia = async (kind: "photo" | "animation", file: File | undefined) => {
    if (!file) return;
    setUploading(kind);
    try {
      const { file_id } =
        kind === "photo"
          ? await uploadBroadcastPhoto(file)
          : await uploadBroadcastAnimation(file);
      // Фото и GIF взаимно исключают друг друга: Telegram не примет оба.
      setPhotoFileId(kind === "photo" ? file_id : null);
      setAnimationFileId(kind === "animation" ? file_id : null);
      toast.success(kind === "photo" ? "Фото загружено" : "GIF загружен");
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось загрузить файл");
    } finally {
      setUploading(null);
    }
  };

  const canLeave1 = title.trim() !== "" && message.trim() !== "";
  const canLeave2 = segment !== "" && audience != null;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-fg">Новая рассылка</h1>
        <p className="mt-0.5 text-base text-fg-muted">
          Сообщение уйдёт живым людям и отозвать его будет нельзя — на последнем
          шаге можно проверить всё на себе.
        </p>
      </header>

      <Steps current={step} onGo={setStep} canLeave1={canLeave1} canLeave2={canLeave2} />

      {step === 1 && (
        <Card>
          <CardHeader
            title="Текст"
            subtitle="Заголовок видят только админы. Сообщение — то, что придёт человеку"
          />
          <CardBody className="space-y-4">
            <Input
              label="Заголовок для админки"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Скидка 30% на Plus, 2 июня"
              hint="Чтобы через месяц найти эту рассылку в списке"
              autoFocus
            />

            <div>
              <div className="mb-1.5 flex items-baseline justify-between gap-2">
                <label htmlFor="broadcast-message" className="text-base font-medium text-fg">
                  Сообщение
                </label>
                <span
                  className={cn(
                    "text-xs tabular-nums",
                    captionOverflow ? "text-danger" : "text-fg-subtle",
                  )}
                >
                  {message.length} из {photoFileId ? CAPTION_LIMIT : TEXT_LIMIT}
                  {photoFileId && " — подпись под фото"}
                </span>
              </div>
              <textarea
                id="broadcast-message"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                maxLength={TEXT_LIMIT}
                rows={9}
                placeholder="Можно размечать: <b>жирный</b>, <i>курсив</i>, <a href=…>ссылка</a>, <blockquote>цитата</blockquote>"
                className="w-full resize-y rounded-md border border-border-control bg-bg-card p-3 text-base leading-relaxed text-fg outline-none focus-visible:border-accent-9"
              />
              {captionOverflow && (
                <p className="mt-1.5 text-base text-danger">
                  С фото текст не может быть длиннее {CAPTION_LIMIT} символов, а
                  сейчас их {message.length}. Массовая рассылка упадёт целиком —
                  уберите фото или сократите текст.
                </p>
              )}
              <p className="mt-1.5 text-xs text-fg-muted">
                Разметку увидите на последнем шаге. Premium-эмодзи вставляются
                как <code>![🙂](tg://emoji?id=…)</code>.
              </p>
            </div>

            <Media
              photoFileId={photoFileId}
              animationFileId={animationFileId}
              uploading={uploading}
              onPick={pickMedia}
              onClear={() => {
                setPhotoFileId(null);
                setAnimationFileId(null);
              }}
            />

            <Nav
              onBack={() => navigate("/broadcasts")}
              backLabel="К списку"
              onNext={() => setStep(2)}
              nextDisabled={!canLeave1}
              nextHint={!canLeave1 ? "Нужны заголовок и текст" : undefined}
            />
          </CardBody>
        </Card>
      )}

      {step === 2 && (
        <Card>
          <CardHeader
            title="Кому"
            subtitle="Число рядом с сегментом — сколько человек в нём сейчас"
          />
          <CardBody className="space-y-4">
            <SegmentPicker value={segment} onChange={setSegment} />
            <Nav
              onBack={() => setStep(1)}
              onNext={() => setStep(3)}
              nextDisabled={!canLeave2}
              nextHint={!canLeave2 ? "Выберите сегмент с известным размером" : undefined}
            />
          </CardBody>
        </Card>
      )}

      {step === 3 && (
        <Card>
          <CardHeader
            title="Кнопки"
            subtitle="Появятся под сообщением. Можно не выбирать ни одной"
          />
          <CardBody className="space-y-4">
            <ButtonPicker value={btn} onChange={setBtn} />
            <Nav
              onBack={() => setStep(2)}
              onNext={() => setStep(4)}
              nextDisabled={!discountReady}
              nextHint={!discountReady ? "Укажите процент скидки" : undefined}
              nextLabel="К проверке"
            />
          </CardBody>
        </Card>
      )}

      {step === 4 && (
        <>
          <SendReview
            segmentLabel={segmentName}
            audience={audience}
            message={message}
            buttons={btn.buttons}
            photo={Boolean(photoFileId)}
            animation={Boolean(animationFileId)}
            captionOverflow={captionOverflow}
            onTest={() => test.mutate()}
            testing={test.isPending}
            testedAt={testedAt}
            onSend={() => send.mutate()}
            sending={send.isPending}
          />
          <div>
            <Button onClick={() => setStep(3)} disabled={send.isPending}>
              ← Назад к кнопкам
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

/** Шаги. Кликабельны только пройденные: прыгать вперёд через пустой
 *  текст незачем, а назад — постоянно нужно. */
function Steps({
  current,
  onGo,
  canLeave1,
  canLeave2,
}: {
  current: Step;
  onGo: (s: Step) => void;
  canLeave1: boolean;
  canLeave2: boolean;
}) {
  const reachable = (n: Step) =>
    n === 1 || (n === 2 && canLeave1) || (n >= 3 && canLeave1 && canLeave2);

  return (
    <ol className="flex flex-wrap items-center gap-1">
      {STEPS.map((s, i) => {
        const active = s.n === current;
        const open = reachable(s.n);
        return (
          <li key={s.n} className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => open && onGo(s.n)}
              disabled={!open}
              aria-current={active ? "step" : undefined}
              className={cn(
                "min-h-tap rounded-md px-2.5 text-base transition-colors",
                active
                  ? "bg-accent-3 font-medium text-accent-12"
                  : open
                    ? "text-fg-muted hover:bg-bg-subtle hover:text-fg"
                    : "cursor-not-allowed text-fg-subtle",
              )}
            >
              <span className="tabular-nums">{s.n}.</span> {s.label}
            </button>
            {i < STEPS.length - 1 && (
              <span className="text-fg-subtle" aria-hidden>
                ›
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Nav({
  onBack,
  backLabel = "Назад",
  onNext,
  nextDisabled,
  nextLabel = "Дальше",
  nextHint,
}: {
  onBack: () => void;
  backLabel?: string;
  onNext: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
  /** Почему «Дальше» недоступна. Серая кнопка без объяснения читается
   *  как поломка интерфейса. */
  nextHint?: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
      <Button onClick={onBack}>← {backLabel}</Button>
      <div className="flex items-center gap-3">
        {nextHint && <span className="text-xs text-fg-muted">{nextHint}</span>}
        <Button variant="primary" onClick={onNext} disabled={nextDisabled}>
          {nextLabel} →
        </Button>
      </div>
    </div>
  );
}

/** Фото или GIF. Не оба сразу: Telegram примет только одно вложение. */
function Media({
  photoFileId,
  animationFileId,
  uploading,
  onPick,
  onClear,
}: {
  photoFileId: string | null;
  animationFileId: string | null;
  uploading: "photo" | "animation" | null;
  onPick: (kind: "photo" | "animation", file: File | undefined) => void;
  onClear: () => void;
}) {
  const attached = photoFileId || animationFileId;

  return (
    <div>
      <div className="mb-1.5 text-base font-medium text-fg">
        Картинка или GIF, если нужны
      </div>

      {attached ? (
        <div className="flex items-center gap-3 rounded-md border border-border bg-bg-subtle px-3 py-2.5">
          {photoFileId ? (
            <ImageIcon className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden />
          ) : (
            <Film className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden />
          )}
          <span className="flex-1 text-base text-fg">
            {photoFileId ? "Фото прикреплено" : "GIF прикреплён"}
          </span>
          <Button size="sm" icon={<X className="h-3.5 w-3.5" />} onClick={onClear}>
            Убрать
          </Button>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <FilePick
            label="Фото, до 10 МБ"
            accept="image/*"
            busy={uploading === "photo"}
            disabled={uploading !== null}
            icon={<ImageIcon className="h-4 w-4" aria-hidden />}
            onPick={(f) => onPick("photo", f)}
          />
          <FilePick
            label="GIF или MP4, до 20 МБ"
            accept="image/gif,video/mp4"
            busy={uploading === "animation"}
            disabled={uploading !== null}
            icon={<Film className="h-4 w-4" aria-hidden />}
            onPick={(f) => onPick("animation", f)}
          />
        </div>
      )}

      <p className="mt-1.5 text-xs text-fg-muted">
        Файл сначала уходит вам в Telegram — иначе не получить его
        идентификатор. Фото и GIF заменяют друг друга.
      </p>
    </div>
  );
}

function FilePick({
  label,
  accept,
  busy,
  disabled,
  icon,
  onPick,
}: {
  label: string;
  accept: string;
  busy: boolean;
  disabled: boolean;
  icon: React.ReactNode;
  onPick: (file: File | undefined) => void;
}) {
  return (
    <label
      className={cn(
        "flex min-h-tap cursor-pointer items-center gap-2.5 rounded-md border border-dashed border-border-control bg-bg-card px-3 py-3 text-base text-fg-muted transition-colors",
        disabled ? "cursor-not-allowed opacity-60" : "hover:bg-bg-subtle hover:text-fg",
      )}
    >
      {busy ? <Spinner /> : icon}
      <span className="flex-1">{busy ? "Загружаю…" : label}</span>
      <input
        type="file"
        accept={accept}
        className="hidden"
        disabled={disabled}
        onChange={(e) => onPick(e.target.files?.[0])}
      />
    </label>
  );
}
