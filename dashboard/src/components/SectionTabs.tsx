import { Suspense } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { cn } from "@/lib/cn";
import { sectionFor } from "@/lib/nav";
import { RouteFallback } from "./RouteFallback";
import { ErrorBoundary } from "./ErrorBoundary";

/**
 * Вкладки внутри раздела.
 *
 * Разделов стало девять вместо шестнадцати, но ни одна страница не исчезла:
 * бывшие пункты сайдбара живут здесь вкладками. Вкладка — это настоящий
 * адрес, а не состояние компонента: ссылку можно прислать коллеге, кнопка
 * «назад» работает, старые адреса редиректят на новые (см. App.tsx).
 *
 * Обычные ссылки, а не role="tablist": содержимое вкладки — отдельный
 * маршрут, а не панель внутри одной страницы. Клавиатура здесь работает как
 * с любыми ссылками, стрелки перехватывать не надо.
 */
export function SectionTabs() {
  const { pathname } = useLocation();
  const section = sectionFor(pathname);
  const tabs = section?.tabs ?? [];

  if (tabs.length === 0) return <Outlet />;

  return (
    <>
      {/* На узком экране лента вкладок прокручивается вбок сама, а не ломает
          страницу горизонтальным скроллом целиком. */}
      <nav
        aria-label={`Вкладки раздела «${section?.label ?? ""}»`}
        // Полосу прокрутки прячем: у ленты вкладок она отъедает 10 px по
        // высоте и читается как элемент интерфейса, хотя аффорданс здесь —
        // сама обрезанная вкладка у края.
        className="-mx-1 mb-5 overflow-x-auto pb-px [&::-webkit-scrollbar]:hidden"
        style={{ scrollbarWidth: "none" }}
      >
        <ul className="flex w-max min-w-full items-center gap-1 border-b border-border px-1">
          {tabs.map((t) => (
            <li key={t.to}>
              <NavLink
                to={t.to}
                end={t.end}
                className={({ isActive }) =>
                  cn(
                    "-mb-px inline-flex min-h-tap items-center whitespace-nowrap border-b-2 px-3 py-2 text-base transition-colors",
                    isActive
                      ? "border-accent-9 font-medium text-fg"
                      : "border-transparent text-fg-muted hover:border-border-strong hover:text-fg",
                  )
                }
              >
                {t.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      {/* Свои границы ожидания и ошибки: и пока грузится чанк вкладки, и если
          вкладка упала, сами вкладки должны остаться на месте. Иначе исчезает
          вся навигация раздела и непонятно, куда возвращаться. */}
      <ErrorBoundary variant="content" resetKey={pathname}>
        <Suspense fallback={<RouteFallback />}>
          <Outlet />
        </Suspense>
      </ErrorBoundary>
    </>
  );
}
