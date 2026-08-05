import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Карточка — прямоугольник с границей на плоском фоне.
 *
 * Тени нет намеренно: §10.4 исследования — все разделители делаются границей,
 * тень остаётся только у плавающих слоёв (поповер, дропдаун, модалка). Градиента
 * под содержимым тоже нет: градиент под данными размывает границу между
 * значениями (§8.3).
 */

export function Card({
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("rounded-lg border border-border bg-bg-card", className)} {...rest}>
      {children}
    </div>
  );
}

/** Шапка карточки: заголовок слева, действия справа. */
export function CardHeader({
  title,
  subtitle,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-3 border-b border-border-subtle px-4 py-3",
        className,
      )}
    >
      <div className="min-w-0">
        <div className="truncate text-lg font-semibold text-fg">{title}</div>
        {subtitle && <div className="mt-0.5 text-xs text-fg-subtle">{subtitle}</div>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("p-4", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardFooter({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 border-t border-border-subtle px-4 py-3",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/**
 * Плитка с числом. Иконки рядом с числом нет сознательно: лишняя графика
 * затрудняет визуальный поиск, без иконок числа заметнее (§8.7).
 * Значение печатается табличными цифрами — это включено глобально в index.css.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  /** Вторая строка: сравнение, доля, «вчера в это же время». */
  hint?: ReactNode;
  /** Окраска числа. По умолчанию нейтральная — цвет тут не украшение. */
  tone?: "neutral" | "money-in" | "money-out";
  className?: string;
}) {
  const toneClass =
    tone === "money-in" ? "text-money-in" : tone === "money-out" ? "text-money-out" : "text-fg";
  return (
    <Card className={cn("p-4", className)}>
      <div className="text-xs font-medium text-fg-subtle">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold", toneClass)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-fg-muted">{hint}</div>}
    </Card>
  );
}
