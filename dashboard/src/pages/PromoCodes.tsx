import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import {
  Button,
  Card,
  CardHeader,
  DensityToggle,
  EmptyFailure,
  EmptyFirstRun,
  EmptyFilter,
  LoadingGate,
  SkeletonTable,
  type Density,
} from "@/components/ui";
import { PromoTable } from "@/components/monetization/PromoTable";
import { PromoCreate } from "@/components/monetization/PromoCreate";
import { promoState } from "@/components/monetization/labels";

/**
 * Промокоды — вторая вкладка раздела «Монетизация».
 *
 * ОТБОР ЖИВЁТ В АДРЕСЕ (?state=). Ссылка на «коды, которые не работают»
 * должна открываться из переписки и из закладки, а не собираться заново
 * тремя кликами.
 *
 * ТРИ РАЗНЫЕ ПУСТОТЫ, И ЭТО ТРИ РАЗНЫХ ЭКРАНА (ux-patterns §3.4):
 *   • кодов нет вообще        → объясняем, зачем раздел, даём «Создать»;
 *   • не совпал отбор         → «ничего не нашлось» и сброс, БЕЗ «создать»;
 *   • запрос упал             → красная плашка со словами, что это отказ.
 * Раньше все три выглядели одинаково — «Нет промокодов», в том числе на
 * упавшем запросе.
 */

type StateFilter = "all" | "live" | "dead";

const FILTERS: Array<[StateFilter, string]> = [
  ["all", "Все"],
  ["live", "Действуют"],
  ["dead", "Не работают"],
];

const DENSITY_KEY = "atlas.promo.density";

function readDensity(): Density {
  const saved = localStorage.getItem(DENSITY_KEY);
  return saved === "compact" || saved === "comfortable" ? saved : "comfortable";
}

export function PromoCodes() {
  const [params, setParams] = useSearchParams();
  const [density, setDensity] = useState<Density>(readDensity);
  const [creating, setCreating] = useState(false);

  useEffect(() => localStorage.setItem(DENSITY_KEY, density), [density]);

  const raw = params.get("state");
  const filter: StateFilter = raw === "live" || raw === "dead" ? raw : "all";

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  const list = useQuery({
    queryKey: ["promo", "list"],
    queryFn: () => endpoints.promoList(),
    refetchInterval: 30_000,
  });

  const all = useMemo(() => list.data ?? [], [list.data]);
  // Отбор считается на клиенте, потому что сервер отдаёт все коды одним
  // списком и их всегда десятки. Появится страничная выдача — отбор
  // обязан уехать на сервер, иначе «действуют: 3» будет означать «3 на
  // текущей странице».
  const rows = useMemo(() => {
    if (filter === "all") return all;
    return all.filter((r) => {
      const live = promoState(r).label === "действует";
      return filter === "live" ? live : !live;
    });
  }, [all, filter]);

  const liveCount = useMemo(
    () => all.filter((r) => promoState(r).label === "действует").length,
    [all],
  );

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Промокоды</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Скидка по коду при покупке. У каждого кода есть лимит применений и
            срок — здесь видно, сколько уже потрачено и когда он кончится.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DensityToggle value={density} onChange={setDensity} className="hidden md:inline-flex" />
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" aria-hidden />}
            onClick={() => setCreating(true)}
          >
            Создать код
          </Button>
        </div>
      </header>

      <div
        role="radiogroup"
        aria-label="Отбор промокодов"
        className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
      >
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={filter === key}
            onClick={() => patch({ state: key === "all" ? null : key })}
            className={
              filter === key
                ? "rounded-sm bg-bg-card px-2 py-1 text-xs font-medium text-fg"
                : "rounded-sm px-2 py-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            }
          >
            {label}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader
          title="Промокоды"
          subtitle={
            list.data
              ? `${fmtNum(all.length)} всего · ${fmtNum(liveCount)} применяются прямо сейчас`
              : "лимит применений и срок — у каждого свои"
          }
        />

        {list.isError ? (
          <div className="p-4">
            <EmptyFailure
              what="список промокодов"
              reason="Список не пришёл. Пустая таблица здесь читалась бы как «кодов нет» — это не так, это отказ запроса."
              onRetry={() => list.refetch()}
            />
          </div>
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={<SkeletonTable rows={6} cols={5} className="m-4 border-0" />}
            message="Считаю применения промокодов"
          >
            {/* `list.data &&` обязателен: первую секунду LoadingGate рисует
                детей, а не скелетон, и без проверки на этой секунде мелькало
                бы «промокодов ещё нет». */}
            {list.data && rows.length === 0 ? (
              <div className="p-4">
                {filter !== "all" ? (
                  <EmptyFilter
                    query={filter === "live" ? "действуют" : "не работают"}
                    onReset={() => patch({ state: null })}
                  />
                ) : (
                  <EmptyFirstRun
                    title="Промокодов ещё нет"
                    description="Промокод снижает цену при покупке на заданный процент. Его можно ограничить числом применений и сроком — оба ограничения видны прямо в списке."
                    actionLabel="Создать первый код"
                    onAction={() => setCreating(true)}
                  />
                )}
              </div>
            ) : (
              <PromoTable rows={rows} density={density} />
            )}
          </LoadingGate>
        )}
      </Card>

      <PromoCreate open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
