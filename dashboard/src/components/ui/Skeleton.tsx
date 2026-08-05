import { cn } from "@/lib/cn";

/**
 * Скелетоны.
 *
 * ГДЕ МОЖНО: таблицы, списки, карточки, плитки — то есть контейнеры с данными.
 * ГДЕ НЕЛЬЗЯ (дословный запрет Carbon, ux-patterns §3.3): тосты, выпадающие
 * меню, пункты списков-выпадашек, модальные окна и индикаторы загрузки. Кнопкам,
 * полям, чекбоксам и переключателям скелетон тоже не нужен — они рисуются сразу
 * в выключенном состоянии.
 *
 * Скелетон обязан двигаться: движение сообщает, что страница не зависла. Блик
 * идёт от .skeleton в index.css и уважает prefers-reduced-motion.
 *
 * Скелетон показывается не сразу, а после ~1 секунды ожидания — см. LoadingGate.
 * До секунды человек не замечает задержки, и мигание прямоугольников только
 * отвлекает.
 *
 * Ещё одно правило Carbon: не всё нужно закрывать серым прямоугольником —
 * часть элементов честнее показать пустым местом.
 */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} aria-hidden />;
}

/** Скелетон строки таблицы. Ширины колонок разные — ровные одинаковые полосы
 *  читаются как элемент оформления, а не как «здесь будут данные». */
export function SkeletonTable({
  rows = 6,
  cols = 5,
  className,
}: {
  rows?: number;
  cols?: number;
  className?: string;
}) {
  const widths = ["w-24", "w-32", "w-16", "w-40", "w-20", "w-28"];
  return (
    <div
      className={cn("rounded-lg border border-border", className)}
      role="status"
      aria-label="Загружаю таблицу"
    >
      <div className="flex items-center gap-4 border-b border-border px-3 py-2.5">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3", widths[i % widths.length])} />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 border-b border-border-subtle px-3 py-2.5 last:border-b-0">
          {Array.from({ length: cols }).map((_, i) => (
            <Skeleton key={i} className={cn("h-3.5", widths[(i + r) % widths.length])} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Скелетон плитки с числом: подпись и само число. */
export function SkeletonTile({ className }: { className?: string }) {
  return (
    <div
      className={cn("rounded-lg border border-border p-4", className)}
      role="status"
      aria-label="Загружаю показатель"
    >
      <Skeleton className="h-3 w-20" />
      <Skeleton className="mt-2 h-7 w-32" />
      <Skeleton className="mt-2 h-3 w-24" />
    </div>
  );
}

/** Скелетон карточки со списком строк. */
export function SkeletonCard({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div
      className={cn("rounded-lg border border-border p-4", className)}
      role="status"
      aria-label="Загружаю карточку"
    >
      <Skeleton className="h-4 w-40" />
      <div className="mt-3 space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3.5", i % 2 ? "w-3/4" : "w-full")} />
        ))}
      </div>
    </div>
  );
}
