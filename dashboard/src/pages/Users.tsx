import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";

import { Button, DensityToggle, Input, type Density } from "@/components/ui";
import { UsersTable, type UsersFilter } from "@/components/users/UsersTable";
import { UserPanel } from "@/components/users/UserPanel";

/**
 * Пользователи — плотная таблица слева, панель деталей справа.
 *
 * ВСЁ СОСТОЯНИЕ ЭКРАНА ЖИВЁТ В АДРЕСЕ: ?filter=, ?q=, ?tg=. Так ссылка на
 * конкретного человека или на отфильтрованный список работает из
 * переписки, из закладки и с плиток сводки. Плитки зоны B ведут сюда с
 * ?filter=active и ?filter=expiring_7d — сломаете разбор параметров, и
 * числа на главной станут мёртвыми ссылками.
 *
 * ПОЧЕМУ ЭТОТ ФАЙЛ КОРОТКИЙ. Он был на 1319 строк и держал в себе восемь
 * форм, шесть мутаций и таблицу покупок. Теперь здесь только расстановка
 * и разбор адреса: таблица, панель, действия и покупки — отдельные
 * компоненты в components/users. Складывать сюда логику не надо, она
 * уедет обратно к тысяче строк.
 *
 * ПЛОТНОСТЬ ТАБЛИЦЫ запоминается между сессиями (research §5): человек,
 * который переключился на компактный режим, ждёт его и завтра.
 */

const DENSITY_KEY = "atlas.users.density";

function readDensity(): Density {
  const saved = localStorage.getItem(DENSITY_KEY);
  return saved === "compact" || saved === "comfortable" ? saved : "comfortable";
}

export function Users() {
  const [params, setParams] = useSearchParams();

  const rawFilter = params.get("filter");
  const filter: UsersFilter =
    rawFilter === "active" || rawFilter === "expiring_7d" ? rawFilter : "all";
  const query = params.get("q") ?? "";
  const tg = params.get("tg");
  const selected = tg && /^\d+$/.test(tg) ? Number(tg) : null;

  // Поле ввода живёт своей жизнью до нажатия «Найти»: поиск по каждой
  // букве дёргал бы сервер на каждый символ, а список — не подсказка.
  const [draft, setDraft] = useState(query);
  const [density, setDensity] = useState<Density>(readDensity);

  useEffect(() => setDraft(query), [query]);
  useEffect(() => localStorage.setItem(DENSITY_KEY, density), [density]);

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Пользователи</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Найти человека, посмотреть подписку и покупки, продлить или отозвать доступ.
          </p>
        </div>
        <DensityToggle value={density} onChange={setDensity} className="hidden md:inline-flex" />
      </header>

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
              placeholder="Telegram ID, @username или часть имени"
              leading={<Search className="h-3.5 w-3.5" />}
              inputMode="search"
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              aria-label="Поиск пользователя"
            />
          </div>
          <Button type="submit" variant="primary">
            Найти
          </Button>
        </form>

        {/* Отборы. Названия те же, что на плитках сводки: человек приходит
            оттуда по клику и должен узнать, куда попал. */}
        <div
          role="radiogroup"
          aria-label="Отбор пользователей"
          className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
        >
          {(
            [
              ["all", "Все"],
              ["active", "Активные"],
              ["expiring_7d", "Истекают за 7 дней"],
            ] as Array<[UsersFilter, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={filter === key}
              onClick={() => patch({ filter: key === "all" ? null : key })}
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
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(380px,440px)]">
        {/* На телефоне открытая карточка заменяет список: две колонки на
            375 px превращаются в кашу, а сравнивать людей между собой на
            этом экране не нужно. */}
        <div className={selected ? "hidden lg:block" : undefined}>
          <UsersTable
            filter={filter}
            query={query}
            selected={selected}
            onSelect={(id) => patch({ tg: String(id) })}
            onResetFilters={() => patch({ filter: null, q: null })}
            density={density}
          />
        </div>

        {selected !== null ? (
          <div className="lg:sticky lg:top-4 lg:self-start">
            <div className="mb-2 lg:hidden">
              <Button size="sm" onClick={() => patch({ tg: null })}>
                ← К списку
              </Button>
            </div>
            <UserPanel telegramId={selected} onClose={() => patch({ tg: null })} />
          </div>
        ) : (
          <div className="hidden rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted lg:block">
            Выберите строку слева — карточка, покупки и действия откроются здесь.
          </div>
        )}
      </div>
    </div>
  );
}
