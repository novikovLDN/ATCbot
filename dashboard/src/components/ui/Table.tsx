import {
  createContext,
  useCallback,
  useContext,
  useRef,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type ThHTMLAttributes,
  type TdHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";

/**
 * Плотная таблица с липкой шапкой.
 *
 * Режимы плотности — из research §10.5: комфортный 40px, компактный 32px,
 * тач 48px. Плотность объявлена именованными режимами, а не подбирается
 * паддингами на каждой странице (модель Carbon, §3.5). Высота шапки совпадает
 * с высотой строки — Carbon требует этого прямо.
 *
 * Липкая шапка обязательна для всего, что длиннее экрана (NN/g про таблицы на
 * мобильном, ux-patterns §4.5). Она работает только если таблица лежит внутри
 * прокручиваемого контейнера — им и является <TableScroll>.
 *
 * Клавиатура: стрелки вверх/вниз ходят по строкам, Home/End — в начало и конец,
 * Enter открывает строку. Это roving tabindex: у активной строки tabindex 0, у
 * остальных −1 (WAI-ARIA APG, ux-patterns §1.5). Значений tabindex больше нуля
 * нигде нет — порядок задаётся разметкой.
 */

export type Density = "comfortable" | "compact" | "touch";

const CELL: Record<Density, string> = {
  comfortable: "h-row px-3 py-2.5 text-base",
  compact: "h-row-compact px-2.5 py-1.5 text-sm",
  touch: "h-row-touch px-3 py-3 text-base",
};

const DensityCtx = createContext<Density>("comfortable");

/** Прокручиваемая обёртка. Без неё липкая шапка прилипать не к чему. */
export function TableScroll({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("relative overflow-auto rounded-lg border border-border", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function Table({
  density = "comfortable",
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLTableElement> & { density?: Density }) {
  const ref = useRef<HTMLTableElement>(null);

  // Перемещение по строкам стрелками. Работает поверх DOM, а не состояния:
  // строк в таблице бывает много, и держать индекс активной строки в React
  // означает перерисовывать всю таблицу на каждое нажатие.
  const onKeyDown = useCallback((e: KeyboardEvent<HTMLTableElement>) => {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const table = ref.current;
    if (!table) return;
    const rows = Array.from(
      table.querySelectorAll<HTMLTableRowElement>('tbody tr[data-nav="1"]'),
    );
    if (rows.length === 0) return;

    const current = document.activeElement?.closest<HTMLTableRowElement>('tr[data-nav="1"]');
    const idx = current ? rows.indexOf(current) : -1;
    let next = idx;
    if (e.key === "ArrowDown") next = Math.min(rows.length - 1, idx + 1);
    if (e.key === "ArrowUp") next = Math.max(0, idx - 1);
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = rows.length - 1;
    if (next === idx || next < 0) return;

    e.preventDefault();
    rows.forEach((r, i) => r.setAttribute("tabindex", i === next ? "0" : "-1"));
    // focus() сам прокручивает строку в видимую область — отдельный
    // scrollIntoView не нужен и мешает липкой шапке.
    rows[next].focus();
  }, []);

  return (
    <DensityCtx.Provider value={density}>
      <table
        ref={ref}
        onKeyDown={onKeyDown}
        className={cn("w-full border-collapse text-left", className)}
        {...rest}
      >
        {children}
      </table>
    </DensityCtx.Provider>
  );
}

export function THead({ className, children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    // Фон непрозрачный: полупрозрачная шапка поверх строк делает нечитаемой
    // первую строку при прокрутке.
    <thead className={cn("sticky top-0 z-10 bg-bg-card", className)} {...rest}>
      {children}
    </thead>
  );
}

export function TBody({ className, children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody className={cn("divide-y divide-border-subtle", className)} {...rest}>
      {children}
    </tbody>
  );
}

export function TH({
  className,
  numeric,
  children,
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  const density = useContext(DensityCtx);
  return (
    <th
      scope="col"
      className={cn(
        "border-b border-border bg-bg-card font-medium text-fg-subtle",
        CELL[density],
        // Числовые колонки выравниваются вправо: так разряды стоят под
        // разрядами, а табличные цифры (tnum включён глобально) дают ровный
        // столбец.
        numeric && "text-right tabular-nums",
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  );
}

export function TD({
  className,
  numeric,
  mono,
  children,
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean; mono?: boolean }) {
  const density = useContext(DensityCtx);
  return (
    <td
      className={cn(
        "align-middle text-fg",
        CELL[density],
        numeric && "text-right tabular-nums",
        // Моноширинный — для ID и хешей. slashed-zero включён в index.css,
        // чтобы ноль не путался с буквой O.
        mono && "font-mono text-xs",
        className,
      )}
      {...rest}
    >
      {children}
    </td>
  );
}

export function TR({
  interactive,
  first,
  tone,
  className,
  children,
  onActivate,
  ...rest
}: HTMLAttributes<HTMLTableRowElement> & {
  /** Строка кликается и попадает в обход стрелками. */
  interactive?: boolean;
  /** Первая интерактивная строка — единственная с tabindex 0 на старте. */
  first?: boolean;
  /** Подсветка строки состоянием: вместо отдельного статусного виджета
   *  состояние читается прямо в таблице (приём Retool, research §4.3). */
  tone?: "none" | "risk" | "failure" | "success";
  onActivate?: () => void;
}) {
  const toneClass =
    tone === "risk"
      ? "bg-risk/[0.06]"
      : tone === "failure"
        ? "bg-danger/[0.06]"
        : tone === "success"
          ? "bg-success/[0.06]"
          : undefined;

  return (
    <tr
      data-nav={interactive ? "1" : undefined}
      tabIndex={interactive ? (first ? 0 : -1) : undefined}
      onClick={interactive ? onActivate : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onActivate?.();
              }
            }
          : undefined
      }
      className={cn(
        toneClass,
        interactive &&
          "cursor-pointer transition-colors hover:bg-bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2",
        className,
      )}
      {...rest}
    >
      {children}
    </tr>
  );
}

/** Переключатель плотности. Выбор стоит сохранять между сессиями (§5). */
export function DensityToggle({
  value,
  onChange,
  className,
}: {
  value: Density;
  onChange: (d: Density) => void;
  className?: string;
}) {
  const items: Array<[Density, string]> = [
    ["comfortable", "Комфортно"],
    ["compact", "Плотно"],
  ];
  return (
    <div
      role="radiogroup"
      aria-label="Плотность таблицы"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5",
        className,
      )}
    >
      {items.map(([key, label]) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "rounded-sm px-2 py-1 text-xs font-medium transition-colors",
            value === key ? "bg-bg-card text-fg" : "text-fg-muted hover:text-fg",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Ячейка «нет значения». Прочерк лучше пустоты: пустая ячейка читается как
 *  ошибка загрузки, прочерк — как «значения нет». */
export function Dash(): ReactNode {
  return <span className="text-fg-subtle">—</span>;
}
