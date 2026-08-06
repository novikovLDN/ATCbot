import { cn } from "@/lib/cn";
import { Input } from "@/components/ui";
import {
  BUTTON_OPTIONS,
  GIFT_REVEAL_PERCENTS,
  NEEDS_DISCOUNT,
} from "./buttonCatalog";

/**
 * Кнопки под сообщением и параметры скидок.
 *
 * ЗАЧЕМ У КАЖДОЙ КНОПКИ ПОЯСНЕНИЕ. Ключи вроде `gift_1y_40` и
 * `share_discount` ничего не говорят о том, что увидит человек после
 * нажатия, а разница между ними — это разные деньги. Раньше пояснения
 * жили в комментариях исходника, то есть были доступны кому угодно,
 * кроме того, кто выбирает кнопку.
 *
 * ПОЛЯ СКИДКИ ПОЯВЛЯЮТСЯ ТОЛЬКО КОГДА ОНИ НУЖНЫ и тогда же становятся
 * обязательными: кнопка «купить со скидкой» без процента — это кнопка,
 * которая обманет получателя.
 */

export interface ButtonState {
  buttons: string[];
  discountPercent: number | "";
  discountHours: number | "";
  giftRevealPercent: number;
}

export function ButtonPicker({
  value,
  onChange,
}: {
  value: ButtonState;
  onChange: (next: ButtonState) => void;
}) {
  const needsDiscount = value.buttons.some((b) => NEEDS_DISCOUNT.includes(b));
  const hasGiftReveal = value.buttons.includes("gift_reveal");

  const toggle = (key: string, on: boolean) =>
    onChange({
      ...value,
      buttons: on
        ? [...value.buttons, key]
        : value.buttons.filter((x) => x !== key),
    });

  return (
    <div className="space-y-4">
      <ul className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
        {BUTTON_OPTIONS.map((b) => {
          const checked = value.buttons.includes(b.key);
          return (
            <li key={b.key}>
              <label
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2.5 transition-colors",
                  checked
                    ? "border-accent-9 bg-accent-3"
                    : "border-border-control bg-bg-card hover:bg-bg-subtle",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => toggle(b.key, e.target.checked)}
                  className="mt-0.5 accent-accent-9"
                />
                <div className="min-w-0">
                  <div
                    className={cn(
                      "text-base",
                      checked ? "font-medium text-accent-12" : "text-fg",
                    )}
                  >
                    {b.label}
                  </div>
                  {b.hint && (
                    <div className="mt-0.5 text-xs leading-snug text-fg-muted">
                      {b.hint}
                    </div>
                  )}
                </div>
              </label>
            </li>
          );
        })}
      </ul>

      {needsDiscount && (
        <section className="rounded-md border border-border bg-bg-subtle p-3">
          <h3 className="text-base font-medium text-fg">Скидка по кнопке</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Столько снимется с цены, когда получатель нажмёт кнопку, и на
            столько часов предложение останется живым.
          </p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Input
              label="Процент"
              type="number"
              min={1}
              max={100}
              value={value.discountPercent}
              onChange={(e) =>
                onChange({
                  ...value,
                  discountPercent: e.target.value === "" ? "" : Number(e.target.value),
                })
              }
              placeholder="30"
              error={
                value.discountPercent === ""
                  ? "Без процента кнопка не заработает"
                  : undefined
              }
            />
            <Input
              label="Часов действия"
              type="number"
              min={1}
              value={value.discountHours}
              onChange={(e) =>
                onChange({
                  ...value,
                  discountHours: e.target.value === "" ? "" : Number(e.target.value),
                })
              }
              placeholder="24"
              hint="Пусто — неделя"
            />
          </div>
        </section>
      )}

      {hasGiftReveal && (
        <section className="rounded-md border border-border bg-bg-subtle p-3">
          <h3 className="text-base font-medium text-fg">Скидка за «Посмотреть подарок»</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Живёт 48 часов после нажатия — этот срок задан в коде бота и здесь
            не меняется.
          </p>
          <div
            role="radiogroup"
            aria-label="Процент скидки за подарок"
            className="mt-2 flex flex-wrap gap-1.5"
          >
            {GIFT_REVEAL_PERCENTS.map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={value.giftRevealPercent === p}
                onClick={() => onChange({ ...value, giftRevealPercent: p })}
                className={cn(
                  "min-h-tap rounded-md border px-3 text-base transition-colors",
                  value.giftRevealPercent === p
                    ? "border-accent-9 bg-accent-3 font-medium text-accent-12"
                    : "border-border-control bg-bg-card text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )}
              >
                {p}%
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
