import { useState } from "react";
import { Search } from "lucide-react";

import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { EmptyFailure, EmptyFilter, Input, LoadingGate, Skeleton } from "@/components/ui";
import { groupSegments, useSegments, type Segment } from "./useSegments";

/**
 * Выбор аудитории.
 *
 * СЕГМЕНТОВ ПЯТЬДЕСЯТ ЧЕТЫРЕ, И ЭТО СЛИШКОМ МНОГО ДЛЯ ПРОСТОГО СПИСКА.
 * Раньше их показывали все подряд, сгруппированными: чтобы дойти до
 * «истёк триал 30 дней назад», надо было прокрутить восемь экранов
 * радиокнопок — и на середине этого пути легко ткнуть не в тот. Поэтому
 * здесь есть поиск по названию, а группы остались как ориентир.
 *
 * РАЗМЕР СЕГМЕНТА — ЧАСТЬ ВЫБОРА, А НЕ УКРАШЕНИЕ. Это число человек
 * увидит потом в подтверждении и наберёт руками, поэтому оно стоит
 * рядом с названием с самого начала: «истёк триал 30 дней назад, 12 400
 * человек» — совсем другое решение, чем «истёк триал 30 дней назад».
 *
 * СЕГМЕНТ, ЧЕЙ РАЗМЕР НЕИЗВЕСТЕН, ВЫБРАТЬ НЕЛЬЗЯ. Сервер помечает
 * упавший подсчёт как -1. Отправлять «неизвестно скольким» — ровно то,
 * от чего защищает весь этот экран.
 */

export function SegmentPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (key: string) => void;
}) {
  const segments = useSegments();
  const [q, setQ] = useState("");

  const needle = q.trim().toLowerCase();
  const matches = (s: Segment) =>
    !needle ||
    s.label.toLowerCase().includes(needle) ||
    s.key.toLowerCase().includes(needle) ||
    (s.description ?? "").toLowerCase().includes(needle);

  const groups = groupSegments(segments.data)
    .map((g) => ({ ...g, items: g.items.filter(matches) }))
    .filter((g) => g.items.length > 0);

  if (segments.isError) {
    return (
      <EmptyFailure
        what="список сегментов"
        reason="Каталог аудиторий не ответил. Без него нельзя узнать, скольким людям уйдёт рассылка, — а вслепую отправлять нечего."
        onRetry={() => segments.refetch()}
      />
    );
  }

  return (
    <div className="space-y-3">
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Найти сегмент: «триал», «истёк», «купили»"
        leading={<Search className="h-3.5 w-3.5" />}
        inputMode="search"
        autoComplete="off"
        aria-label="Поиск по сегментам"
      />

      <LoadingGate
        loading={segments.isLoading}
        skeleton={
          <div className="space-y-2">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        }
        message="Считаю, сколько человек в каждом сегменте"
      >
        {groups.length === 0 ? (
          <EmptyFilter query={q.trim()} onReset={() => setQ("")} />
        ) : (
          <div className="space-y-4">
            {groups.map((g) => (
              <section key={g.group}>
                <h3 className="mb-1.5 text-xs font-medium text-fg-subtle">{g.group}</h3>
                <ul className="space-y-1">
                  {g.items.map((s) => (
                    <li key={s.key}>
                      <SegmentOption
                        segment={s}
                        checked={value === s.key}
                        onSelect={() => onChange(s.key)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </LoadingGate>
    </div>
  );
}

function SegmentOption({
  segment,
  checked,
  onSelect,
}: {
  segment: Segment;
  checked: boolean;
  onSelect: () => void;
}) {
  const unknown = segment.count === -1;
  const empty = segment.count === 0;

  return (
    <label
      className={cn(
        "flex cursor-pointer items-start justify-between gap-3 rounded-md border px-3 py-2.5 transition-colors",
        checked
          ? "border-accent-9 bg-accent-3"
          : "border-border-control bg-bg-card hover:bg-bg-subtle",
        // Неизвестный размер выбрать нельзя: курсор и приглушение
        // объясняют это до клика, подпись справа — словами.
        unknown && "cursor-not-allowed opacity-60",
      )}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <input
          type="radio"
          name="segment"
          checked={checked}
          disabled={unknown}
          onChange={onSelect}
          className="mt-1 accent-accent-9"
        />
        <div className="min-w-0">
          <div className={cn("text-base", checked ? "font-medium text-accent-12" : "text-fg")}>
            {segment.label}
          </div>
          {segment.description && (
            <div className="mt-0.5 text-xs leading-snug text-fg-muted">
              {segment.description}
            </div>
          )}
        </div>
      </div>

      <span className="shrink-0 text-right">
        {unknown ? (
          <span className="text-xs text-danger">размер не посчитался</span>
        ) : (
          <>
            <span className="block text-base font-medium tabular-nums text-fg">
              {fmtNum(segment.count)}
            </span>
            <span className="block text-xs text-fg-subtle">
              {empty ? "никого" : "человек"}
            </span>
          </>
        )}
      </span>
    </label>
  );
}
