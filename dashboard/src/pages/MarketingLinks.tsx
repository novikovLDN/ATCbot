import { useSearchParams } from "react-router-dom";

import { StatsLinks } from "@/components/monetization/StatsLinks";
import { PromoLinks } from "@/components/monetization/PromoLinks";

/**
 * Ссылки — третья вкладка раздела «Монетизация».
 *
 * ДВА РАЗНЫХ ИНСТРУМЕНТА ПОД ОДНИМ ИМЕНЕМ, И ПУТАТЬ ИХ ДОРОГО:
 *
 *   СТАТИСТИКА  ссылка ничего не выдаёт, она считает: сколько перешло,
 *               сколько зарегистрировалось, сколько заплатило.
 *   ПРОМО       ссылка выдаёт награду по переходу — подписку, скидку
 *               или гигабайты. Это расход, и у неё есть лимит и срок.
 *
 * ВЫБОР ЖИВЁТ В АДРЕСЕ (?kind=promo), а не в состоянии компонента: на
 * промо-ссылку присылают ссылку коллеге, и она обязана открыться сразу
 * на нужном списке. Кнопка «назад» по той же причине работает.
 */
export function MarketingLinks() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") === "promo" ? "promo" : "stats";

  const setKind = (next: "stats" | "promo") => {
    const usp = new URLSearchParams(params);
    if (next === "stats") usp.delete("kind");
    else usp.set("kind", next);
    setParams(usp, { replace: true });
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-fg">Ссылки</h1>
        <p className="mt-0.5 text-base text-fg-muted">
          {kind === "stats"
            ? "Считают переходы и доводят их до покупки. Ничего не выдают."
            : "Выдают награду по переходу. У каждой есть лимит активаций и срок."}
        </p>
      </header>

      <div
        role="radiogroup"
        aria-label="Тип ссылок"
        className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
      >
        {(
          [
            ["stats", "Со статистикой"],
            ["promo", "С наградой"],
          ] as Array<["stats" | "promo", string]>
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={kind === key}
            onClick={() => setKind(key)}
            className={
              kind === key
                ? "rounded-sm bg-bg-card px-3 py-1.5 text-xs font-medium text-fg"
                : "rounded-sm px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            }
          >
            {label}
          </button>
        ))}
      </div>

      {kind === "promo" ? <PromoLinks /> : <StatsLinks />}
    </div>
  );
}
