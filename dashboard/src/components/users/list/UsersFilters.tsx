/**
 * Filter bar for the user listing.
 *
 * The design rule that matters here: a filtered table must never look
 * like an unfiltered one. Every active constraint is echoed back as a
 * removable chip with the resulting count, because the alternative —
 * a control panel whose state you have to go and read — is how people
 * end up drawing conclusions from a subset they forgot they applied.
 *
 * Presets exist because the questions an operator actually asks are a
 * short list ("who lapses this week", "who signed up today") and each one
 * is otherwise four separate controls.
 */
import { useState } from "react";
import { Search, X } from "lucide-react";
import type { UserListFilters } from "@/types/user";
import { fmtNum } from "@/lib/format";

interface Props {
  value: UserListFilters;
  onChange: (next: UserListFilters) => void;
  total: number | undefined;
  isFetching: boolean;
}

/** ISO date N days from now, for the relative presets. */
function isoInDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}
function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

const PRESETS: { label: string; hint: string; build: () => UserListFilters }[] = [
  {
    label: "Истекают за 7 дней",
    hint: "Активная подписка, которая заканчивается на этой неделе",
    build: () => ({ has_sub: true, expires_before: isoInDays(7), sort: "expires_at", order: "asc" }),
  },
  {
    label: "Новые за 7 дней",
    hint: "Зарегистрировались на этой неделе",
    build: () => ({ created_after: isoDaysAgo(7), sort: "created_at", order: "desc" }),
  },
  {
    label: "Без подписки",
    hint: "Зарегистрированы, но не платят",
    build: () => ({ has_sub: false, sort: "created_at", order: "desc" }),
  },
  {
    label: "Выдано вручную",
    hint: "Доступ выдан админом, а не оплачен",
    build: () => ({ source: "admin", sort: "created_at", order: "desc" }),
  },
];

/** Human label for each active constraint, used by the chip row. */
function describeFilters(f: UserListFilters): { key: keyof UserListFilters; label: string }[] {
  const out: { key: keyof UserListFilters; label: string }[] = [];
  if (f.q) out.push({ key: "q", label: `Поиск: ${f.q}` });
  if (f.has_sub !== undefined)
    out.push({ key: "has_sub", label: f.has_sub ? "С подпиской" : "Без подписки" });
  if (f.source) out.push({ key: "source", label: `Источник: ${f.source}` });
  if (f.created_after) out.push({ key: "created_after", label: `Рег. с ${f.created_after}` });
  if (f.created_before) out.push({ key: "created_before", label: `Рег. до ${f.created_before}` });
  if (f.expires_before)
    out.push({ key: "expires_before", label: `Истекает до ${f.expires_before}` });
  return out;
}

export function UsersFilters({ value, onChange, total, isFetching }: Props) {
  const [draft, setDraft] = useState(value.q ?? "");
  const chips = describeFilters(value);

  function patch(p: Partial<UserListFilters>) {
    // Any filter change invalidates the current page: staying on page 7
    // of a result set that just shrank to two pages shows an empty table
    // and reads as "no matches".
    onChange({ ...value, ...p, offset: 0 });
  }

  function clearOne(key: keyof UserListFilters) {
    const next = { ...value };
    delete next[key];
    if (key === "q") setDraft("");
    onChange({ ...next, offset: 0 });
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <form
          className="relative min-w-0 flex-1 basis-full sm:max-w-xs sm:basis-auto"
          onSubmit={(e) => {
            e.preventDefault();
            patch({ q: draft.trim() || undefined });
          }}
        >
          <label htmlFor="users-search" className="sr-only">
            Поиск по имени или Telegram ID
          </label>
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ash"
            aria-hidden="true"
          />
          <input
            id="users-search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Имя или Telegram ID"
            className="input pl-10"
            autoComplete="off"
          />
        </form>

        {/* Presets are one-shot actions, not a selected state, so they are
            capsule buttons rather than a Segmented control. */}
        <div className="-mx-1 max-w-full overflow-x-auto px-1 scrollbar-none">
          <div className="capsule-nav" role="group" aria-label="Быстрые фильтры">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                title={p.hint}
                onClick={() => {
                  setDraft("");
                  onChange({ ...p.build(), limit: value.limit, offset: 0 });
                }}
                className="capsule-tab h-8 px-3 text-[12px]"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* The count sits next to the chips, not somewhere else on the page:
          "which filters" and "how many are left" is one thought. */}
      <div className="flex flex-wrap items-center gap-2" aria-live="polite">
        <span className="t-mute tabular text-[13px]">
          {total === undefined
            ? "Загрузка…"
            : `${fmtNum(total)} ${chips.length ? "по фильтру" : "всего"}`}
          {isFetching && total !== undefined && " · обновляется"}
        </span>

        {chips.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => clearOne(c.key)}
            aria-label={`Убрать фильтр: ${c.label}`}
            className="capsule-label tap-target text-ink outline-none transition-colors duration-[var(--dur-instant)] hover:bg-tile-4 focus-visible:ring-2 focus-visible:ring-accent/45"
          >
            {c.label}
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        ))}

        {chips.length > 1 && (
          <button
            type="button"
            onClick={() => {
              setDraft("");
              onChange({ limit: value.limit, offset: 0 });
            }}
            className="btn-ghost px-3 py-1 text-[13px]"
          >
            Сбросить всё
          </button>
        )}
      </div>
    </div>
  );
}
