import { useLocation } from "react-router-dom";
import { Search, ShieldCheck } from "lucide-react";
import { LiveIndicator } from "./LiveIndicator";
import { ThemeToggle } from "./ThemeToggle";
import { sectionFor } from "@/lib/nav";

/**
 * Шапка.
 *
 * Её раньше не было вовсе: переключателя темы в интерфейсе не существовало
 * (только на витрине примитивов, которой нет в проде), а индикатор связи висел
 * плавающей плашкой поверх контента в правом нижнем углу.
 *
 * Три вещи слева направо: где я, вызов палитры с подписью шортката, состояние
 * связи и тема. Подпись шортката на кнопке — не украшение: это единственный
 * способ узнать про ⌘K, не читая документации (NN/g, ux-patterns §1.4).
 */

/** На маке пишем ⌘, везде остальном — Ctrl. Иначе подпись врёт половине. */
const isMac =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { pathname } = useLocation();
  const section = sectionFor(pathname);

  return (
    <header
      className="flex shrink-0 items-center gap-2 border-b border-border bg-bg px-3 md:px-6"
      style={{
        // Вырез и «уши» айфона обходим здесь, в единственном месте: раньше
        // отступ безопасной зоны считался и в body, и в контейнере контента,
        // и на телефоне с вырезом это давало около 100 px пустоты.
        paddingTop: "env(safe-area-inset-top)",
        paddingLeft: "max(0.75rem, env(safe-area-inset-left))",
        paddingRight: "max(0.75rem, env(safe-area-inset-right))",
      }}
    >
      <div className="flex h-14 min-w-0 flex-1 items-center gap-2">
        {/* Логотип виден только на телефоне: на десктопе он в сайдбаре. */}
        <div className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-accent-9 text-white md:hidden">
          <ShieldCheck className="h-3.5 w-3.5" strokeWidth={2.25} aria-hidden />
        </div>
        <span className="truncate text-base font-medium text-fg md:text-fg-muted md:font-normal">
          {section?.label ?? "Atlas"}
        </span>
      </div>

      <div className="flex h-14 shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={onOpenPalette}
          className="flex min-h-tap items-center gap-2 rounded-md border border-border-control bg-bg-card px-2 py-1.5 text-base text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          aria-label="Открыть палитру команд"
        >
          <Search className="h-4 w-4 shrink-0" aria-hidden />
          <span className="hidden lg:inline">Команды</span>
          <kbd className="rounded-sm border border-border bg-bg-subtle px-1.5 py-0.5 font-mono text-2xs font-medium text-fg-subtle">
            {isMac ? "⌘K" : "Ctrl K"}
          </kbd>
        </button>

        <LiveIndicator />
        <ThemeToggle />
      </div>
    </header>
  );
}
