import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import {
  Button,
  DensityToggle,
  EmptyFailure,
  Input,
  LoadingGate,
  SkeletonTile,
  StatTile,
  type Density,
} from "@/components/ui";
import {
  ReferrersTable,
  SORT_LABEL,
  type ReferrerSort,
} from "@/components/monetization/ReferrersTable";
import { ReferrerPanel } from "@/components/monetization/ReferrerPanel";

/**
 * Рефералы — четвёртая вкладка раздела «Монетизация».
 *
 * ВСЁ СОСТОЯНИЕ ЭКРАНА В АДРЕСЕ: ?sort=, ?q=, ?id=. Ссылка на конкретного
 * партнёра работает из переписки и из закладки; кнопка «назад» тоже.
 *
 * ЭКРАН ТОЛЬКО ЧИТАЕТ. Процент кешбэка и выплаты меняются не здесь —
 * кнопок правки тут нет намеренно, чтобы «посмотреть, кто сколько
 * привёл» не соседствовало с «изменить условия» на расстоянии промаха.
 *
 * ОТКАЗ НЕ РИСУЕТ НОЛЬ. «0 ₽ дохода с партнёрки» и «не смогли посчитать»
 * читаются совершенно по-разному, и первое — успокаивающая неправда.
 */

const DENSITY_KEY = "atlas.referrals.density";

function readDensity(): Density {
  const saved = localStorage.getItem(DENSITY_KEY);
  return saved === "compact" || saved === "comfortable" ? saved : "comfortable";
}

export function Referrals() {
  const [params, setParams] = useSearchParams();
  const [density, setDensity] = useState<Density>(readDensity);

  useEffect(() => localStorage.setItem(DENSITY_KEY, density), [density]);

  const rawSort = params.get("sort");
  const sort: ReferrerSort =
    rawSort === "invited_count" || rawSort === "cashback_paid" ? rawSort : "total_revenue";
  const query = params.get("q") ?? "";
  const rawId = params.get("id");
  const selected = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null;

  // Поле ввода живёт своей жизнью до нажатия «Найти»: запрос на каждую
  // букву дёргал бы сервер, а список — не подсказка.
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  const overall = useQuery({
    queryKey: ["referrals", "overall"],
    queryFn: endpoints.referralsOverall,
    staleTime: 60_000,
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Рефералы</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Кто приводит покупателей, сколько они принесли и сколько за это
            выплачено кешбэком.
          </p>
        </div>
        <DensityToggle value={density} onChange={setDensity} className="hidden md:inline-flex" />
      </header>

      {overall.isError ? (
        <EmptyFailure
          what="итоги партнёрской программы"
          reason="Не смогли посчитать. Нули здесь читались бы как «партнёрка ничего не приносит» — это не так."
          onRetry={() => overall.refetch()}
        />
      ) : (
        <LoadingGate
          loading={overall.isLoading}
          skeleton={
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <SkeletonTile key={i} />
              ))}
            </div>
          }
          message="Считаю итоги партнёрки"
        >
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Партнёров"
              value={fmtNum(overall.data?.total_referrers)}
              hint="привели хотя бы одного человека"
            />
            <StatTile
              label="Приглашённых"
              value={fmtNum(overall.data?.total_referrals)}
              hint="всего пришло по ссылкам"
            />
            <StatTile
              label="Выручка с партнёрки"
              value={fmtRub(overall.data?.total_revenue)}
              tone="money-in"
              hint="покупки приглашённых"
            />
            <StatTile
              label="Выплачено кешбэком"
              value={fmtRub(overall.data?.total_cashback_paid)}
              tone="money-out"
              hint="начислено партнёрам на баланс"
            />
          </div>
        </LoadingGate>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <form
          className="flex min-w-[260px] flex-1 items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            patch({ q: draft.trim() || null });
          }}
        >
          <div className="flex-1">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Telegram ID или @username партнёра"
              leading={<Search className="h-3.5 w-3.5" />}
              inputMode="search"
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              aria-label="Поиск партнёра"
            />
          </div>
          <Button type="submit" variant="primary">
            Найти
          </Button>
        </form>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-fg-muted" htmlFor="referrals-sort">
            Сортировка
          </label>
          <select
            id="referrals-sort"
            value={sort}
            onChange={(e) => patch({ sort: e.target.value === "total_revenue" ? null : e.target.value })}
            className="h-9 rounded-md border border-border-control bg-bg-card px-2 text-base text-fg"
          >
            {(Object.keys(SORT_LABEL) as ReferrerSort[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABEL[k]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
        {/* На телефоне открытая карточка заменяет список: две колонки на
            375 px превращаются в кашу. */}
        <div className={selected !== null ? "hidden lg:block" : undefined}>
          <ReferrersTable
            sort={sort}
            query={query}
            selected={selected}
            onSelect={(id) => patch({ id: String(id) })}
            onResetFilters={() => patch({ q: null, sort: null })}
            density={density}
          />
        </div>

        {selected !== null ? (
          <div className="lg:sticky lg:top-4 lg:self-start">
            <div className="mb-2 lg:hidden">
              <Button size="sm" onClick={() => patch({ id: null })}>
                ← К списку
              </Button>
            </div>
            <ReferrerPanel referrerId={selected} onClose={() => patch({ id: null })} />
          </div>
        ) : (
          <div className="hidden rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted lg:block">
            Выберите партнёра слева — здесь будет видно, кого он привёл и что
            ему начислили.
          </div>
        )}
      </div>
    </div>
  );
}
