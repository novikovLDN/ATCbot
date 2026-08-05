import { Suspense, lazy, useCallback, useState } from "react";
import { useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { MobileNav } from "./MobileNav";
import { TopBar } from "./TopBar";
import { InstallHint } from "./InstallHint";
import { RouteTransition } from "./RouteTransition";
import { ErrorBoundary } from "./ErrorBoundary";
import { useShellHotkeys } from "@/lib/hotkeys";
import { RouteFallback } from "./RouteFallback";

/**
 * Оболочка приложения: сайдбар, шапка, область контента, нижняя навигация.
 *
 * Три вещи, которые здесь решены и стоят объяснения:
 *
 * 1. Отступ безопасной зоны считается один раз. Раньше `body` в index.css
 *    добавлял padding-top/bottom от env(safe-area-inset-*), и поверх этого то
 *    же самое делал контейнер контента. На айфоне с вырезом набегало около
 *    100 px пустоты. Теперь вырез обходит только шапка, а нижнюю полосу —
 *    только мобильная навигация.
 *
 * 2. Граница ошибок стоит вокруг контента, а не только вокруг всего
 *    приложения. Упавший экран не должен гасить оболочку: с живым сайдбаром
 *    человек уйдёт в другой раздел сам. Ключ сброса — pathname: ушли с
 *    битого экрана, ошибка забыта.
 *
 * 3. Палитра ⌘K грузится лениво и монтируется при первом вызове. Слушатель
 *    клавиш при этом стоит всегда — он стоит десяток строк, а не сотню
 *    килобайт.
 */

const CommandPalette = lazy(() => import("./CommandPalette"));

export function Layout() {
  const { pathname } = useLocation();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Один раз смонтировав палитру, держим её: повторное открытие не должно
  // ждать сеть, даже если чанк уже в кэше.
  const [paletteMounted, setPaletteMounted] = useState(false);

  const openPalette = useCallback(() => {
    setPaletteMounted(true);
    setPaletteOpen(true);
  }, []);

  useShellHotkeys(openPalette);

  return (
    <div className="flex h-full">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenPalette={openPalette} />

        {/* pb-24 на телефоне — под нижнюю панель; на десктопе её нет. */}
        <main className="flex-1 overflow-y-auto pb-24 md:pb-0">
          <div
            className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-8"
            style={{
              paddingLeft: "max(1rem, env(safe-area-inset-left))",
              paddingRight: "max(1rem, env(safe-area-inset-right))",
            }}
          >
            <ErrorBoundary variant="content" resetKey={pathname}>
              <Suspense fallback={<RouteFallback />}>
                <RouteTransition />
              </Suspense>
            </ErrorBoundary>
          </div>
        </main>
      </div>

      <MobileNav />
      <InstallHint />

      {paletteMounted && (
        <Suspense fallback={null}>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        </Suspense>
      )}
    </div>
  );
}
