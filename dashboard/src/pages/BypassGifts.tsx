import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import {
  Button,
  DensityToggle,
  EmptyFailure,
  LoadingGate,
  SkeletonTile,
  StatTile,
  type Density,
} from "@/components/ui";
import { GiftsTable, type GiftFilter } from "@/components/monetization/GiftsTable";
import { GiftPanel } from "@/components/monetization/GiftPanel";
import { GiftCreate } from "@/components/monetization/GiftCreate";

/**
 * Подарочные гигабайты — пятая вкладка раздела «Монетизация».
 *
 * ЭТО РАСХОДНАЯ ЧАСТЬ РАЗДЕЛА. Остальные четыре вкладки про то, как
 * деньги приходят; эта — про то, как трафик уходит. Поэтому плитки
 * сверху считают не «сколько ссылок создано», а сколько гигабайт уже
 * роздано: число, из которого следует решение.
 *
 * ВСЁ СОСТОЯНИЕ В АДРЕСЕ: ?state=all показывает и удалённые, ?id=
 * открывает карточку. Ссылка на конкретный код работает из переписки.
 *
 * ОТКАЗ НЕ РИСУЕТ НОЛЬ. «0 ГБ выдано» и «не смогли посчитать» читаются
 * по-разному, и первое — успокаивающая неправда.
 */

const DENSITY_KEY = "atlas.bgift.density";

function readDensity(): Density {
  const saved = localStorage.getItem(DENSITY_KEY);
  return saved === "compact" || saved === "comfortable" ? saved : "comfortable";
}

export function BypassGifts() {
  const [params, setParams] = useSearchParams();
  const [density, setDensity] = useState<Density>(readDensity);
  const [creating, setCreating] = useState(false);

  useEffect(() => localStorage.setItem(DENSITY_KEY, density), [density]);

  const filter: GiftFilter = params.get("state") === "all" ? "all" : "active";
  const rawId = params.get("id");
  const selected = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null;

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  const summary = useQuery({
    queryKey: ["bgift", "summary"],
    queryFn: endpoints.bgiftSummary,
    staleTime: 60_000,
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Подарочные гигабайты</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Ссылки, по которым человек получает гигабайты обхода. У каждой есть
            предел активаций и срок — здесь видно, сколько уже роздано.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DensityToggle value={density} onChange={setDensity} className="hidden md:inline-flex" />
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" aria-hidden />}
            onClick={() => setCreating(true)}
          >
            Создать ссылку
          </Button>
        </div>
      </header>

      {summary.isError ? (
        <EmptyFailure
          what="итоги по подарочным ссылкам"
          reason="Не смогли посчитать. Нули здесь читались бы как «ничего не роздано» — это не так."
          onRetry={() => summary.refetch()}
        />
      ) : (
        <LoadingGate
          loading={summary.isLoading}
          skeleton={
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <SkeletonTile key={i} />
              ))}
            </div>
          }
          message="Считаю выданные гигабайты"
        >
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Роздано трафика"
              value={`${fmtNum(summary.data?.total_gb_granted)} ГБ`}
              tone="money-out"
              hint="за всё время, по всем ссылкам"
            />
            <StatTile
              label="Активаций"
              value={fmtNum(summary.data?.total_redemptions)}
              hint="сколько раз забрали подарок"
            />
            <StatTile
              label="Ссылок работает"
              value={fmtNum(summary.data?.active_links)}
              hint="не удалены и срок не вышел"
            />
            <StatTile
              label="Ссылок всего"
              value={fmtNum(summary.data?.total_links)}
              hint="включая истёкшие и удалённые"
            />
          </div>
        </LoadingGate>
      )}

      <div
        role="radiogroup"
        aria-label="Отбор ссылок"
        className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
      >
        {(
          [
            ["active", "Действующие"],
            ["all", "Все, включая удалённые"],
          ] as Array<[GiftFilter, string]>
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={filter === key}
            onClick={() => patch({ state: key === "active" ? null : key })}
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
        <div className={selected !== null ? "hidden lg:block" : undefined}>
          <GiftsTable
            filter={filter}
            selected={selected}
            onSelect={(id) => patch({ id: String(id) })}
            onResetFilters={() => patch({ state: "all" })}
            onCreate={() => setCreating(true)}
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
            <GiftPanel
              linkId={selected}
              onClose={() => patch({ id: null })}
              onDeleted={() => patch({ id: null })}
            />
          </div>
        ) : (
          <div className="hidden rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted lg:block">
            Выберите ссылку слева — здесь будут ссылка для рассылки, расход и
            список тех, кто её активировал.
          </div>
        )}
      </div>

      <GiftCreate open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
