import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api";
import { EmptyFailure, LoadingGate, SkeletonCard } from "@/components/ui";
import { GlobalDiscount } from "@/components/monetization/GlobalDiscount";
import { ComboPrices, PriceTable } from "@/components/monetization/PriceTable";

/**
 * Цены и скидки — первая вкладка раздела «Монетизация».
 *
 * ЗДЕСЬ ДЕНЬГИ, И ЭТО МЕНЯЕТ ПРАВИЛА. Изменение цены действует на всех
 * будущих покупателей сразу; опечатка в поле — прямой убыток или
 * остановка продаж, и заметить её по экрану невозможно. Поэтому между
 * вводом числа и его применением всегда стоит окно «было → станет» в
 * рублях (components/monetization/PriceDiff). Уберёте окно — экран снова
 * станет опасным.
 *
 * ПОЧЕМУ ЭТОТ ФАЙЛ КОРОТКИЙ. Он был на 509 строк и держал в себе две
 * формы, четыре мутации и разбор дат. Здесь остались только загрузка,
 * разделение строк на правимые и комбо, и расстановка. Форма правки —
 * PriceTable, форма скидки — GlobalDiscount.
 *
 * ПОРЯДОК БЛОКОВ НЕ СЛУЧАЕН. Скидка сверху, потому что она двигает весь
 * прайс сразу: увидев её, человек правильно прочитает числа в таблице
 * ниже. Комбо в самом низу — его отсюда не правят.
 *
 * ОШИБКА НЕ РИСУЕТ ПУСТОЙ ПРАЙС. Отказ запроса и «тарифов нет» — разные
 * вещи, и пустая таблица на месте упавшего запроса читалась бы как
 * «продавать нечего».
 */
export function Pricing() {
  const tariffs = useQuery({
    queryKey: ["pricing", "tariffs"],
    queryFn: () => endpoints.pricingTariffs(),
    refetchInterval: 60_000,
  });
  const discount = useQuery({
    queryKey: ["pricing", "global-discount"],
    queryFn: () => endpoints.pricingGetGlobalDiscount(),
    refetchInterval: 60_000,
  });

  const rows = useMemo(() => tariffs.data ?? [], [tariffs.data]);
  // Комбо приходит теми же строками, но с editable=false: его цена живёт
  // в config.COMBO_TARIFFS и ни переопределением, ни скидкой не меняется.
  const editable = useMemo(() => rows.filter((r) => r.editable !== false), [rows]);
  const combo = useMemo(() => rows.filter((r) => r.editable === false), [rows]);

  // Процент, который сейчас реально применяется. Скидка с истёкшим
  // сроком на сервере не действует, и предпросмотр обязан считать так же,
  // иначе он покажет цены, которых нет.
  const activePercent = (() => {
    const d = discount.data;
    if (!d || d.global_discount_percent <= 0) return 0;
    if (d.discount_until_at && new Date(d.discount_until_at).getTime() <= Date.now()) return 0;
    return d.global_discount_percent;
  })();

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-fg">Цены и скидки</h1>
        <p className="mt-0.5 text-base text-fg-muted">
          Что платит покупатель за каждый тариф и период. Любая правка
          действует на всех новых покупателей сразу — перед сохранением
          экран показывает, какие суммы изменятся.
        </p>
      </header>

      {discount.isError ? (
        <EmptyFailure
          what="настройки глобальной скидки"
          reason="Не смогли прочитать текущую скидку. Пока она неизвестна, править цены опасно: непонятно, что получится на выходе."
          onRetry={() => discount.refetch()}
        />
      ) : (
        <LoadingGate
          loading={discount.isLoading}
          skeleton={<SkeletonCard lines={3} />}
          message="Читаю настройки скидки"
        >
          <GlobalDiscount current={discount.data} rows={editable} />
        </LoadingGate>
      )}

      {tariffs.isError ? (
        <EmptyFailure
          what="прайс тарифов"
          reason="Список цен не пришёл. Пустая таблица здесь читалась бы как «тарифов нет» — это не так, это отказ запроса."
          onRetry={() => tariffs.refetch()}
        />
      ) : (
        <LoadingGate
          loading={tariffs.isLoading}
          skeleton={
            <div className="space-y-3">
              <SkeletonCard lines={4} />
              <SkeletonCard lines={4} />
            </div>
          }
          message="Считаю цены по тарифам"
        >
          {/* `tariffs.data &&` обязателен: первую секунду LoadingGate
              рисует детей, а не скелетон. Без проверки на этой секунде
              мелькала бы пустая страница «тарифов нет». */}
          {tariffs.data && editable.length === 0 && combo.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted">
              В конфиге нет ни одного тарифа с ценой. Продавать сейчас нечего —
              это состояние конфигурации, а не сбой.
            </div>
          ) : (
            <>
              <PriceTable rows={editable} discountPercent={activePercent} />
              <div className="pt-1">
                <ComboPrices rows={combo} />
              </div>
            </>
          )}
        </LoadingGate>
      )}
    </div>
  );
}
