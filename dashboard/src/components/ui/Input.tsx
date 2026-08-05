import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { AlertCircle } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Поле ввода с подписью, подсказкой и ошибкой.
 *
 * Три вещи, которые здесь не случайны:
 *  1. Ошибка живёт рядом с полем, а не в тосте наверху экрана — NN/g:
 *     сообщение об ошибке показывается у источника (ux-patterns §3.5).
 *  2. Ошибка помечена не только красным: рядом иконка и текст. Красно-зелёная
 *     дальтонизация — самая частая, цвет не может быть единственным каналом
 *     (research §4.11).
 *  3. Граница поля берётся из --border-control (3:1 к фону): по границе
 *     опознают сам компонент, и WCAG 1.4.11 требует для неё контраст.
 *     Обычная граница карточки даёт 1.5:1 и для поля не годится.
 *
 * Скелетон полю не нужен — Carbon запрещает скелетоны для полей и кнопок
 * (ux-patterns §3.3). Пока данных нет, поле показывается пустым и выключенным.
 */
export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: string;
  /** Поясняющий текст под полем. Скрывается, когда есть ошибка. */
  hint?: string;
  /** Текст ошибки. Непустая строка переводит поле в состояние ошибки. */
  error?: string;
  /** Иконка или единица измерения внутри поля. Имена leading/trailing, а не
   *  prefix/suffix: у <input> уже есть HTML-атрибут prefix со строковым типом,
   *  и одноимённое свойство ломает типизацию. */
  leading?: ReactNode;
  trailing?: ReactNode;
  /** Моноширинный ввод — для ID, промокодов, хешей. */
  mono?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, leading, trailing, mono, className, id, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined;

  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label htmlFor={inputId} className="text-xs font-medium text-fg-muted">
          {label}
        </label>
      )}

      <div
        className={cn(
          "flex min-h-9 items-center gap-2 rounded-md border bg-bg-card px-2.5",
          "transition-colors focus-within:border-accent-9",
          error ? "border-danger" : "border-border-control",
        )}
      >
        {leading && <span className="shrink-0 text-fg-subtle">{leading}</span>}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(
            "min-w-0 flex-1 bg-transparent py-1.5 text-base text-fg outline-none",
            "placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:opacity-50",
            mono && "font-mono",
            className,
          )}
          {...rest}
        />
        {trailing && <span className="shrink-0 text-fg-subtle">{trailing}</span>}
      </div>

      {error ? (
        <div id={`${inputId}-error`} className="flex items-start gap-1 text-xs text-danger">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : hint ? (
        <div id={`${inputId}-hint`} className="text-xs text-fg-subtle">
          {hint}
        </div>
      ) : null}
    </div>
  );
});
