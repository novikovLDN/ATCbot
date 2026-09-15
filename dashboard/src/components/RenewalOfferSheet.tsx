/**
 * Overview «Истекают за 7 дней» → «Предложить продление со скидкой».
 * A normal broadcast (POST /broadcasts/renewal-offer) to active paid
 * subscriptions ending within 7 days, with the existing «🎁 Купить со
 * скидкой N%» button. Two steps in one sheet: edit → «Отправить N
 * пользователям?».
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Users as UsersIcon } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useIdempotencyKeys } from "@/hooks/useIdempotencyKeys";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { Segmented, Switch } from "@/components/ui/controls";
import { ErrorState, Skeleton } from "@/components/ui/states";

const FIELD_LABEL = "t-mute mb-1.5 text-[13px]";
const HOUR_PRESETS = [24, 48, 72];

function render(text: string, discount: number, hours: number): string {
  return text.split("{discount}").join(String(discount)).split("{hours}").join(String(hours));
}

function sanitize(html: string): string {
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/on\w+="[^"]*"/gi, "")
    .replace(/javascript:/gi, "");
}

export function RenewalOfferSheet({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const info = useQuery({
    queryKey: ["broadcasts", "renewal-offer"],
    queryFn: endpoints.renewalOfferInfo,
    staleTime: 0,
  });
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [discount, setDiscount] = useState(15);
  const [hoursDraft, setHoursDraft] = useState("72");
  const [excludeAuto, setExcludeAuto] = useState(true);
  const [step, setStep] = useState<"edit" | "confirm">("edit");

  // First load: the first template and the backend defaults.
  useEffect(() => {
    if (!info.data || templateId !== null) return;
    const first = info.data.templates[0];
    setTemplateId(first?.id ?? "");
    setText(first?.text ?? "");
    setDiscount(info.data.default_discount);
    setHoursDraft(String(info.data.default_hours));
  }, [info.data, templateId]);

  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const firstRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    firstRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    document.addEventListener("keydown", onKey);
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      html.style.overflow = prev;
    };
  }, []);

  const maxHours = info.data?.max_hours ?? 168;
  const hours = Number(hoursDraft);
  const hoursValid = Number.isInteger(hours) && hours >= 1 && hours <= maxHours;
  const audience = info.data ? (excludeAuto ? info.data.audience.manual : info.data.audience.all) : null;
  const segmentLabel = info.data ? (excludeAuto ? info.data.labels.manual : info.data.labels.all) : "";
  const canSend = Boolean(info.data) && text.trim().length > 0 && hoursValid && (audience ?? 0) > 0;

  const keys = useIdempotencyKeys();
  const send = useMutation({
    mutationFn: () => {
      const body = {
        message: text,
        discount_percent: discount,
        discount_hours: hours,
        exclude_auto_renew: excludeAuto,
        confirm: true,
      };
      return endpoints.renewalOfferSend(body, keys.opts("renewal", body));
    },
    onSuccess: (r) => {
      keys.settle("renewal");
      toast.success(`Рассылка #${r.broadcast_id} запущена на ${fmtNum(r.audience)} получателей`);
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      onClose();
    },
    onError: (e: unknown) => {
      keys.settle("renewal", e);
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить");
      setStep("edit");
    },
  });

  return createPortal(
    <>
      <div className="sheet-backdrop" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Предложить продление со скидкой" className="sheet">
        <div className="sheet-grabber" aria-hidden="true" />
        <div className="mx-auto flex max-w-[520px] flex-col gap-4">
          {step === "edit" ? (
            <>
              <div>
                <h2 className="text-[20px] font-semibold leading-[25px]">Предложить продление со скидкой</h2>
                <p className="t-mute mt-1 text-[13px] leading-5">
                  Платные подписки, которые закончатся в ближайшие 7 дней. Обычная рассылка с кнопкой
                  «🎁 Купить со скидкой»: скидка применится после нажатия и действует выбранное время.
                  Если у человека уже есть скидка больше — останется большая.
                </p>
              </div>

              {info.isLoading ? (
                <div className="flex flex-col gap-2" role="status" aria-label="Загружаю…">
                  <Skeleton className="h-[52px] w-full rounded-row" />
                  <Skeleton className="h-[140px] w-full rounded-row" />
                </div>
              ) : info.isError ? (
                <ErrorState className="bg-tile-3" error={info.error} onRetry={() => info.refetch()} />
              ) : (
                <>
                  <div>
                    <div className={FIELD_LABEL}>Текст</div>
                    <div className="flex flex-col gap-2" role="radiogroup" aria-label="Готовый текст">
                      {info.data!.templates.map((t, i) => (
                        <button
                          key={t.id}
                          ref={i === 0 ? firstRef : undefined}
                          type="button"
                          role="radio"
                          aria-checked={templateId === t.id}
                          onClick={() => {
                            setTemplateId(t.id);
                            setText(t.text);
                          }}
                          className={cn(
                            "min-h-[44px] rounded-row px-4 py-2.5 text-left text-[14px] transition-colors",
                            templateId === t.id ? "bg-tile-4 font-medium text-ink" : "bg-tile-3 hover:bg-tile-4",
                          )}
                        >
                          {t.title}
                        </button>
                      ))}
                    </div>
                    <textarea
                      className="input mt-2 min-h-[140px] resize-y font-sans leading-relaxed"
                      value={text}
                      maxLength={4000}
                      onChange={(e) => setText(e.target.value)}
                      aria-label="Текст сообщения (HTML)"
                    />
                    <p className="t-mute mt-1.5 text-[12px] leading-4">
                      {"{discount}"} и {"{hours}"} подставятся при отправке. Можно править текст.
                    </p>
                  </div>

                  <div>
                    <div className={FIELD_LABEL}>Скидка</div>
                    <Segmented
                      full
                      label="Процент скидки"
                      value={discount}
                      options={info.data!.discount_choices.map((p) => ({ value: p, label: `${p} %` }))}
                      onChange={setDiscount}
                    />
                  </div>

                  <div>
                    <div className={FIELD_LABEL}>Скидка действует, часов</div>
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        className="input tabular w-[96px]"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={maxHours}
                        value={hoursDraft}
                        onChange={(e) => setHoursDraft(e.target.value)}
                        aria-label="Часов действия скидки"
                        aria-invalid={!hoursValid}
                      />
                      {HOUR_PRESETS.map((h) => (
                        <button
                          key={h}
                          type="button"
                          aria-pressed={hours === h}
                          onClick={() => setHoursDraft(String(h))}
                          className={cn("badge tap-target", hours === h ? "badge-accent" : "bg-tile-1 t-body")}
                        >
                          {h} ч
                        </button>
                      ))}
                    </div>
                    {!hoursValid && (
                      <p role="alert" className="mt-1.5 text-[13px] text-danger">
                        От 1 до {maxHours} часов.
                      </p>
                    )}
                  </div>

                  <div className="flex min-h-[44px] items-center justify-between gap-3 rounded-row bg-tile-3 px-4 py-2.5">
                    <span className="text-[14px] leading-5">
                      Не отправлять тем, у кого включено автопродление
                      <span className="t-mute block text-[12px]">продлятся сами — скидка только срежет выручку</span>
                    </span>
                    <Switch label="Без автопродления" checked={excludeAuto} onChange={setExcludeAuto} />
                  </div>

                  <div className="flex items-center justify-between gap-3 rounded-row bg-tile-3 px-4 py-3">
                    <span className="t-mute min-w-0 text-[13px]">{segmentLabel}</span>
                    <span className="badge-accent tabular inline-flex shrink-0 items-center gap-1">
                      <UsersIcon className="h-3 w-3" /> {audience !== null && audience >= 0 ? fmtNum(audience) : "—"}
                    </span>
                  </div>

                  <div className="rounded-row bg-tile-3 p-4">
                    <div className="t-mute mb-2 text-[13px]">Так увидит пользователь</div>
                    <div
                      className="whitespace-pre-wrap text-[14px] leading-relaxed"
                      dangerouslySetInnerHTML={{
                        __html: sanitize(render(text, discount, hoursValid ? hours : 0)),
                      }}
                    />
                    <div className="mt-3 rounded-row bg-tile-1 px-3 py-2 text-center text-[14px] font-medium">
                      🎁 Купить со скидкой {discount}%
                    </div>
                  </div>
                </>
              )}

              <div className="grid grid-cols-2 gap-2">
                <button type="button" className="btn-secondary w-full" onClick={onClose}>
                  Отмена
                </button>
                <button
                  type="button"
                  className="btn-primary w-full"
                  disabled={!canSend}
                  onClick={() => setStep("confirm")}
                >
                  Отправить
                </button>
              </div>
            </>
          ) : (
            <>
              <h2 className="text-[20px] font-semibold leading-[25px]">
                Отправить {fmtNum(audience ?? 0)} пользователям?
              </h2>
              <div className="t-body text-[15px] leading-6">
                <div>{segmentLabel}</div>
                <div>
                  Скидка {discount}% на {hours} ч, кнопка «🎁 Купить со скидкой {discount}%».
                </div>
                <div className="t-mute mt-1 text-[13px]">Отменить после отправки нельзя.</div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  className="btn-secondary w-full"
                  onClick={() => setStep("edit")}
                  disabled={send.isPending}
                >
                  Назад
                </button>
                <button
                  type="button"
                  className="btn-primary w-full"
                  onClick={() => send.mutate()}
                  disabled={send.isPending || !canSend}
                >
                  {send.isPending && <Spinner />}
                  Отправить {fmtNum(audience ?? 0)}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
