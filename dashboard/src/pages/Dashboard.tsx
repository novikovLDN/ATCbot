import { Link } from "react-router-dom";
import { Megaphone } from "lucide-react";

import { Button } from "@/components/ui";
import { MoneyToday } from "@/components/summary/MoneyToday";
import { BusinessState } from "@/components/summary/BusinessState";
import { NeedsAttention } from "@/components/summary/NeedsAttention";
import { EventFeed } from "@/components/summary/EventFeed";

/**
 * Сводка — главный экран. Четыре зоны сверху вниз, и ничего больше.
 *
 *   A  деньги: выручка за сегодня против вчера в то же время суток;
 *   B  состояние бизнеса: четыре кликабельных числа;
 *   C  требует внимания: 0–10 конкретных объектов;
 *   D  лента событий: последние 20.
 *
 * ПОЧЕМУ ЭТОТ ФАЙЛ КОРОТКИЙ
 * Он был на 2337 строк и держал восемнадцать блоков с независимыми
 * переключателями окон (24ч/7д/30д, 1д/7д/30д, 7/30/90/180). Главного
 * числа в нём не было — все блоки конкурировали за внимание, и каждый
 * тянул свой запрос: одиннадцать эндпоинтов с одиннадцатью таймерами.
 * Теперь зона = компонент = один запрос, а этот файл только расставляет
 * их по порядку. Класть сюда логику зоны не надо — она уедет обратно к
 * двум тысячам строк.
 *
 * ЧЕГО ЗДЕСЬ БОЛЬШЕ НЕТ И ГДЕ ЭТО ТЕПЕРЬ
 *   разбивки по провайдерам      → /payments и /analytics/stats
 *   почасовой график             → /analytics
 *   воронка конверсии            → /analytics
 *   тарифные разрезы             → /analytics
 *   реферальная аналитика        → /analytics/stats и /monetization/referrals
 *   ARPU / LTV / средний чек     → /analytics
 *   сегменты рассылки            → /analytics/stats и /broadcasts/new
 *   сверка с Remnawave           → /service
 * Ни одна функция не удалена — изменилась глубина (NN/g «Reduce Clutter
 * Without Reducing Capability», research §9.2).
 *
 * ЗАПРОСОВ ЧЕТЫРЕ, ПО ОДНОМУ НА ЗОНУ, и у каждой свой темп: деньги раз в
 * 30 секунд, состояние и проблемы раз в минуту, лента раз в минуту плюс
 * толчок из WebSocket. Общий ключ кэша ['summary'] — по нему лента и
 * сбрасывает соседей, когда шина сообщает о новом платеже.
 */
export function Dashboard() {
  return (
    <div className="mx-auto max-w-[1200px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">Сводка</h1>
          <p className="mt-0.5 text-base text-fg-muted">
            Что с деньгами прямо сейчас и что требует рук.
          </p>
        </div>
        {/* Единственное первичное действие на экране. Всё остальное —
            переходы по числам и строкам списков. */}
        <Link to="/broadcasts/new">
          <Button variant="primary" icon={<Megaphone className="h-3.5 w-3.5" />}>
            Новая рассылка
          </Button>
        </Link>
      </header>

      <MoneyToday />
      <BusinessState />
      <NeedsAttention />
      <EventFeed />
    </div>
  );
}
