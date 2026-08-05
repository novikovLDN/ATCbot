import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Кнопка.
 *
 * Варианты:
 *  primary   — главное действие на экране, ровно одно;
 *  secondary — обычное действие;
 *  ghost     — действие в плотном ряду (строка таблицы, шапка), без рамки;
 *  danger    — необратимое или разрушительное действие.
 *
 * Про danger: он сплошного цвета, а не бледный контур. Опасная кнопка обязана
 * читаться как опасная до нажатия, а не после (ux-patterns §2.2). При этом сам
 * по себе цвет ничего не подтверждает — за danger-кнопкой должен стоять либо
 * баннер отмены (UndoBanner), либо диалог подтверждения (ConfirmDialog).
 *
 * Про loading: кнопка не превращается в скелетон — Carbon прямо запрещает
 * скелетоны для кнопок (ux-patterns §3.3). Вместо этого крутилка внутри и
 * блокировка повторного нажатия, чтобы платёж не ушёл дважды.
 */

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  // Заливка кобальтом ступени 9 в обеих темах, поэтому белый текст здесь
  // безопасен: 5.9:1 в светлой, 4.6:1 в тёмной.
  primary: "bg-accent-9 text-white hover:bg-accent-10 active:bg-accent-10",
  secondary:
    "border border-border-control bg-bg-card text-fg hover:bg-bg-subtle active:bg-bg-elevated",
  ghost: "text-fg-muted hover:bg-bg-subtle hover:text-fg active:bg-bg-elevated",
  // text-bg-card, а не text-white: в тёмной теме семантические токены светлые,
  // и белая надпись на них не читается. Цвет карточки переворачивается сам.
  danger: "bg-danger text-bg-card hover:opacity-90 active:opacity-100",
};

const SIZES: Record<Size, string> = {
  // Высоты кратны 4 и не меньше 24px интерактивной цели (WCAG 2.2 SC 2.5.8).
  sm: "h-7 gap-1.5 px-2 text-xs",
  md: "h-9 gap-2 px-3 text-base",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  /** Иконка слева. Только как дополнение к слову, не вместо него. */
  icon?: ReactNode;
  /** Сочетание клавиш, отрисовывается справа серым — так его и запоминают
   *  (NN/g про акселераторы, ux-patterns §1.4). */
  shortcut?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, icon, shortcut, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={rest.type ?? "button"}
      // aria-busy, чтобы скринридер сообщил о работе, а не молчал.
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center justify-center whitespace-nowrap rounded-md",
        "font-medium transition-colors duration-100",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
      ) : (
        icon
      )}
      {children}
      {shortcut && (
        <kbd className="ml-1 font-mono text-2xs font-normal opacity-60">{shortcut}</kbd>
      )}
    </button>
  );
});
