import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { Skeleton } from "./Skeleton";

/**
 * Карточка с одним числом.
 *
 * ЧТО ИЗ НЕЁ УБРАНО И ПОЧЕМУ (research §9.1, п. «отделка»; §8.3, §8.7).
 *
 * 1. ГРАДИЕНТ ПОД ЧИСЛОМ. Раньше каждая карточка рисовала слой
 *    `bg-gradient-to-br from-<tone>/12 to-bg-card` под содержимым. Градиент в
 *    области данных запрещён: он меняет яркость фона по диагонали, из-за чего
 *    одно и то же число слева и справа читается с разным контрастом, а
 *    границы между соседними карточками размываются. Разделитель — граница,
 *    и только она (§10.4).
 *
 * 2. ИКОНКА В КАЖДОЙ КАРТОЧКЕ. Отдельный кружок с иконкой справа от числа
 *    стоял на всех карточках подряд и не нёс смысла: иконка «кошелёк» рядом с
 *    подписью «Доход» повторяет подпись. Лишняя графика замедляет визуальный
 *    поиск нужного числа в ряду из четырёх (§8.7). Пропс `icon` компонент
 *    по-прежнему ПРИНИМАЕТ, но не рисует — так вызывающие страницы, которые
 *    его передают, продолжают собираться. Убирать пропс из типа нельзя:
 *    сломается сборка чужих экранов.
 *
 * 3. СОБСТВЕННЫЙ СЕРЫЙ ПРЯМОУГОЛЬНИК НА ЗАГРУЗКЕ. Было
 *    `<span className="h-8 w-28 rounded-md bg-bg-elevated" />` — статичная
 *    заглушка мимо общего скелетона. Статичный прямоугольник не отличим от
 *    пустого блока: непонятно, грузится или уже загрузилось и там ничего нет.
 *    Теперь общий `Skeleton` с бликом, который уважает prefers-reduced-motion.
 *
 * ЧТО СЛОМАЕТСЯ ПРИ НЕВЕРНОЙ ПРАВКЕ. Если вернуть градиент — поедет контраст
 * числа, и `npm run check:contrast` этого не поймает: скрипт считает пары
 * токенов, а не то, что нарисовано поверх. Если сделать `icon` обязательным —
 * упадёт сборка Referrals.tsx и BypassGifts.tsx.
 *
 * Реализация живёт здесь, в `ui/`. `@/components/StatCard` — тонкий реэкспорт
 * ради старых мест вызова.
 */

export interface StatCardProps {
  label: string;
  value: string;
  /** Вторая строка: знаменатель метрики, оговорка, сравнение. */
  hint?: string;
  /** Короткая плашка справа от числа: дельта, доля. */
  pill?: string;
  /**
   * @deprecated Принимается ради совместимости, но не рисуется — см. п. 2 в
   * шапке файла. Новый код передавать не должен.
   */
  icon?: LucideIcon;
  /** Окраска числа. Цвет здесь — усиление подписи, а не единственный смысл. */
  tone?: "default" | "success" | "warning" | "danger" | "accent";
  loading?: boolean;
  className?: string;
}

const VALUE_TONES: Record<NonNullable<StatCardProps["tone"]>, string> = {
  default: "text-fg",
  accent: "text-accent-text",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

export function StatCard({
  label,
  value,
  hint,
  pill,
  tone = "default",
  loading,
  className,
}: StatCardProps) {
  return (
    <div className={cn("card p-4", className)}>
      <div className="text-xs font-medium text-fg-subtle">{label}</div>
      <div className="mt-1 flex min-w-0 items-baseline gap-2">
        {loading ? (
          // Габариты повторяют будущее число, чтобы карточка не прыгала,
          // когда данные приедут.
          <Skeleton className="h-8 w-28" />
        ) : (
          <span
            className={cn(
              "block min-w-0 truncate text-2xl font-semibold tabular-nums md:text-3xl",
              VALUE_TONES[tone],
            )}
          >
            {value}
          </span>
        )}
        {pill && !loading && <span className="stat-pill shrink-0">{pill}</span>}
      </div>
      {hint && <div className="mt-1 truncate text-xs text-fg-muted">{hint}</div>}
    </div>
  );
}
