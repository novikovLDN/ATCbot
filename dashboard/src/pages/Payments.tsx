import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Button, DensityToggle, type Density } from "@/components/ui";
import { MoneyStrip } from "@/components/payments/MoneyStrip";
import { PaymentsFeed, type FeedStatus } from "@/components/payments/PaymentsFeed";
import { PaymentPanel } from "@/components/payments/PaymentPanel";
import { PaymentErrors } from "@/components/payments/PaymentErrors";
import { providerLabel } from "@/components/payments/labels";

/**
 * Платежи — две задачи на одном экране, разведённые вкладками.
 *
 *   ЛЕНТА   что купили за окно; строка открывает разбор конкретной
 *           покупки: провайдер, счёт, кто платил, выдан ли доступ.
 *   ОТКАЗЫ  почему оплата не прошла: стадия, исход, что делать.
 *
 * СО СВОДКИ СЮДА ВЕДЁТ ССЫЛКА ?focus=errors — плитка «сорвалось оплат за
 * 24 ч». Она обязана открывать сразу вкладку отказов, а не страницу
 * вообще: число, ведущее «примерно туда», ничем не лучше текста.
 * Ссылка ?purchase=<id> открывает разбор конкретной покупки — по ней
 * приходят из карточки пользователя.
 *
 * ЧЕГО ЗДЕСЬ БОЛЬШЕ НЕТ И ГДЕ ЭТО ТЕПЕРЬ. Круговая диаграмма по
 * провайдерам и столбики по тарифам уехали в /analytics/stats
 * (research §9.2: глубина, а не удаление). Экран платежей — про разбор
 * конкретных строк, а не про форму пирога; заодно с него ушёл recharts,
 * который тянулся в чанк ради двух картинок.
 */

const WINDOWS: Array<{ label: string; hours: number }> = [
  { label: "24 часа", hours: 24 },
  { label: "7 дней", hours: 168 },
  { label: "30 дней", hours: 720 },
  { label: "90 дней", hours: 2160 },
];

const STATUSES: Array<[FeedStatus, string]> = [
  ["", "Все"],
  ["paid", "Оплачены"],
  ["pending", "Ждут оплаты"],
  ["expired", "Счёт истёк"],
];

/** Следующее окно из списка. Последнее остаётся последним. */
function nextWindow(hours: number): number {
  const wider = WINDOWS.find((w) => w.hours > hours);
  return wider ? wider.hours : WINDOWS[WINDOWS.length - 1].hours;
}

const DENSITY_KEY = "atlas.payments.density";

function readDensity(): Density {
  const saved = localStorage.getItem(DENSITY_KEY);
  return saved === "compact" || saved === "comfortable" ? saved : "comfortable";
}

export function Payments() {
  const [params, setParams] = useSearchParams();
  const [density, setDensity] = useState<Density>(readDensity);

  useEffect(() => localStorage.setItem(DENSITY_KEY, density), [density]);

  const tab = params.get("focus") === "errors" ? "errors" : "feed";
  const hours = Number(params.get("hours")) || 168;
  const status = (params.get("status") ?? "") as FeedStatus;
  const provider = params.get("provider") ?? "";
  const purchaseParam = params.get("purchase");
  const purchaseId = purchaseParam && /^\d+$/.test(purchaseParam) ? Number(purchaseParam) : null;

  const patch = (next: Record<string, string | null>) => {
    const usp = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") usp.delete(key);
      else usp.set(key, value);
    }
    setParams(usp, { replace: true });
  };

  // Список провайдеров для фильтра берём из разбивки за то же окно:
  // так в фильтре нет способов оплаты, которыми в этом периоде никто не
  // пользовался, и рядом с каждым стоит число.
  const providers = useQuery({
    queryKey: ["payments", "by-provider", hours],
    queryFn: () => endpoints.paymentsByProvider(hours),
    staleTime: 60_000,
  });

  // Счётчик отказов на вкладке. Отказ этого запроса не рисует ноль:
  // вкладка просто остаётся без числа, а не сообщает, что сбоев нет.
  const errorsSummary = useQuery({
    queryKey: ["payments", "errors", "summary", hours],
    queryFn: () => endpoints.paymentsErrorsSummary(hours),
    refetchInterval: 60_000,
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Платежи</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Что купили за период и почему часть оплат не прошла.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={hours}
            onChange={(e) => patch({ hours: e.target.value })}
            aria-label="Период"
            className="h-9 rounded-md border border-border-control bg-bg-card px-2 text-base text-fg"
          >
            {WINDOWS.map((w) => (
              <option key={w.hours} value={w.hours}>
                {w.label}
              </option>
            ))}
          </select>
          <DensityToggle
            value={density}
            onChange={setDensity}
            className="hidden md:inline-flex"
          />
        </div>
      </header>

      {/* Вкладки. Отказы — не свёрнутая секция внизу, а равноправная
          половина экрана: с ними приходят по жалобе, и искать их
          прокруткой неправильно. */}
      <div
        role="tablist"
        aria-label="Раздел платежей"
        className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
      >
        <TabButton
          active={tab === "feed"}
          onClick={() => patch({ focus: null })}
          label="Лента платежей"
        />
        <TabButton
          active={tab === "errors"}
          onClick={() => patch({ focus: "errors" })}
          label={
            errorsSummary.data && errorsSummary.data.total > 0
              ? `Отказы · ${fmtNum(errorsSummary.data.total)}`
              : "Отказы"
          }
        />
      </div>

      {tab === "errors" ? (
        <PaymentErrors hours={hours} />
      ) : (
        <>
          <MoneyStrip hours={hours} />

          <div className="flex flex-wrap items-center gap-2">
            <div
              role="radiogroup"
              aria-label="Состояние платежа"
              className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
            >
              {STATUSES.map(([key, label]) => (
                <button
                  key={key || "all"}
                  type="button"
                  role="radio"
                  aria-checked={status === key}
                  onClick={() => patch({ status: key || null })}
                  className={
                    status === key
                      ? "rounded-sm bg-bg-card px-2 py-1 text-xs font-medium text-fg"
                      : "rounded-sm px-2 py-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
                  }
                >
                  {label}
                </button>
              ))}
            </div>

            <select
              value={provider}
              onChange={(e) => patch({ provider: e.target.value || null })}
              aria-label="Способ оплаты"
              className="h-8 rounded-md border border-border-control bg-bg-card px-2 text-xs text-fg"
            >
              <option value="">Любой способ оплаты</option>
              {(providers.data ?? []).map((p) => (
                <option key={p.provider} value={p.provider}>
                  {providerLabel(p.provider)} · {fmtNum(p.count)}
                </option>
              ))}
            </select>

            {providers.isError && (
              <span className="text-xs text-fg-muted">
                Список способов оплаты не загрузился — фильтр по нему сейчас
                неполный.
                <button
                  type="button"
                  onClick={() => providers.refetch()}
                  className="ml-1 text-accent-text underline-offset-2 hover:underline"
                >
                  Повторить
                </button>
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
            <div className={purchaseId !== null ? "hidden lg:block" : undefined}>
              <PaymentsFeed
                hours={hours}
                status={status}
                provider={provider}
                selected={purchaseId}
                onSelect={(id) => patch({ purchase: String(id) })}
                onResetFilters={() => patch({ status: null, provider: null })}
                // Следующее окно из списка, а не «умножить на четыре»:
                // произвольное число не совпало бы ни с одним пунктом
                // выбора периода, и селектор наверху опустел бы.
                onWiden={() => patch({ hours: String(nextWindow(hours)) })}
                density={density}
              />
            </div>

            {purchaseId !== null ? (
              <div className="lg:sticky lg:top-4 lg:self-start">
                <div className="mb-2 lg:hidden">
                  <Button size="sm" onClick={() => patch({ purchase: null })}>
                    ← К ленте
                  </Button>
                </div>
                <PaymentPanel
                  purchaseId={purchaseId}
                  onClose={() => patch({ purchase: null })}
                />
              </div>
            ) : (
              <div className="hidden rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted lg:block">
                Выберите платёж — здесь будет видно, кто платил, чем платил и
                выдан ли доступ.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        active
          ? "rounded-sm bg-bg-card px-3 py-1.5 text-xs font-medium text-fg"
          : "rounded-sm px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
      }
    >
      {label}
    </button>
  );
}
