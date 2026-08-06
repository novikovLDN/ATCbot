import { Search, X } from "lucide-react";

import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import {
  CATEGORY_HINTS,
  CATEGORY_LABELS,
  EVENT_CATEGORIES,
  type EventCategory,
} from "@/lib/events";
import { Button, Input } from "@/components/ui";

/**
 * Фильтры журнала: по типу события, по человеку, по времени.
 *
 * Три вопроса, на которые отвечает экран, — «что произошло», «кто это
 * сделал» и «когда». Под каждый ровно один орган управления, и все три
 * видны сразу: спрятанный за «показать фильтры» фильтр не применяют.
 *
 * СЧЁТЧИКИ НА КАТЕГОРИЯХ НЕ ЗАВИСЯТ ОТ ВЫБОРА КАТЕГОРИИ. Их считает
 * сервер по остальным фильтрам, иначе выбранная категория обнуляла бы
 * соседей и это читалось бы как «других событий нет».
 *
 * КАТЕГОРИИ ВЫБИРАЮТСЯ ПАЧКОЙ. Одиночный выбор заставлял бы смотреть
 * «деньги» и «доступ» двумя заходами, хотя разбор инцидента — это
 * обычно именно они вместе.
 *
 * ПУСТОЙ ФИЛЬТР — НЕ ФИЛЬТР. Ни одна категория не выбрана значит «все»,
 * а не «ничего»: иначе первое же снятие галки давало бы пустой экран.
 */

export interface EventsFilterValue {
  categories: EventCategory[];
  /** Окно в часах; 0 — за всё время. */
  hours: number;
  /** telegram_id. Пустая строка — фильтр не применён. */
  who: string;
  q: string;
}

export const EMPTY_FILTER: EventsFilterValue = {
  categories: [],
  hours: 0,
  who: "",
  q: "",
};

export function isFilterActive(v: EventsFilterValue): boolean {
  return (
    v.categories.length > 0 || v.hours !== 0 || v.who.trim() !== "" || v.q.trim() !== ""
  );
}

/** Словами — для текста пустого состояния: «под условие … записей нет». */
export function describeFilter(v: EventsFilterValue): string {
  const parts: string[] = [];
  if (v.categories.length) {
    parts.push(v.categories.map((c) => CATEGORY_LABELS[c]).join(" + "));
  }
  const window = HOURS.find((h) => h.value === v.hours);
  if (v.hours !== 0 && window) parts.push(window.label.toLowerCase());
  if (v.who.trim()) parts.push(`человек ${v.who.trim()}`);
  if (v.q.trim()) parts.push(`текст «${v.q.trim()}»`);
  return parts.join(", ");
}

const HOURS: Array<{ value: number; label: string }> = [
  { value: 24, label: "Сутки" },
  { value: 24 * 7, label: "Неделя" },
  { value: 24 * 30, label: "Месяц" },
  { value: 0, label: "Всё время" },
];

export function EventFilters({
  value,
  onChange,
  counts,
}: {
  value: EventsFilterValue;
  onChange: (v: EventsFilterValue) => void;
  /** По категориям. undefined — ещё не считали (первая загрузка). */
  counts?: Record<string, number>;
}) {
  const toggleCategory = (c: EventCategory) => {
    const has = value.categories.includes(c);
    onChange({
      ...value,
      categories: has
        ? value.categories.filter((x) => x !== c)
        : [...value.categories, c],
    });
  };

  return (
    <div className="space-y-3 rounded-lg border border-border bg-bg-card p-3">
      {/* Тип события. group + aria-pressed, а не радиокнопки: выбор
          множественный, и роль radio врала бы скринридеру. */}
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Тип события">
        {EVENT_CATEGORIES.map((c) => {
          const active = value.categories.includes(c);
          const n = counts?.[c];
          return (
            <button
              key={c}
              type="button"
              aria-pressed={active}
              title={CATEGORY_HINTS[c]}
              onClick={() => toggleCategory(c)}
              className={cn(
                "inline-flex min-h-tap items-center gap-1.5 rounded-md border px-2.5 py-1 text-base transition-colors",
                active
                  ? "border-accent-9 bg-accent-3 font-medium text-accent-12"
                  : "border-border-control bg-bg-card text-fg-muted hover:bg-bg-subtle hover:text-fg",
              )}
            >
              {CATEGORY_LABELS[c]}
              {n != null && (
                <span className="tabular-nums text-xs text-fg-subtle">{fmtNum(n)}</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        {/* Окно по времени. Выбор один, поэтому настоящая радиогруппа. */}
        <div
          role="radiogroup"
          aria-label="Период"
          className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
        >
          {HOURS.map((h) => (
            <button
              key={h.value}
              type="button"
              role="radio"
              aria-checked={value.hours === h.value}
              onClick={() => onChange({ ...value, hours: h.value })}
              className={cn(
                "rounded-sm px-2.5 py-1.5 text-xs font-medium transition-colors",
                value.hours === h.value
                  ? "bg-bg-card text-fg"
                  : "text-fg-muted hover:text-fg",
              )}
            >
              {h.label}
            </button>
          ))}
        </div>

        <div className="w-40">
          <Input
            label="Человек"
            placeholder="telegram_id"
            inputMode="numeric"
            mono
            value={value.who}
            // Только цифры: поле ищет по telegram_id и автора, и адресата.
            // Пропустить сюда «@ivan» значило бы молча ничего не найти.
            onChange={(e) =>
              onChange({ ...value, who: e.target.value.replace(/\D+/g, "") })
            }
          />
        </div>

        <div className="min-w-[12rem] flex-1">
          <Input
            label="Поиск по тексту"
            placeholder="действие или детали"
            leading={<Search className="h-4 w-4" aria-hidden />}
            value={value.q}
            onChange={(e) => onChange({ ...value, q: e.target.value })}
          />
        </div>

        {isFilterActive(value) && (
          <Button
            icon={<X className="h-3.5 w-3.5" />}
            onClick={() => onChange(EMPTY_FILTER)}
          >
            Сбросить
          </Button>
        )}
      </div>
    </div>
  );
}
