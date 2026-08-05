import { type LucideIcon } from "lucide-react";

/**
 * УСТАРЕЛО. Один универсальный «пусто» на все случаи — та самая ошибка, о
 * которой пишет ux-patterns §3.4: первый запуск, пустой фильтр, нет прав и
 * отказ загрузки требуют разного текста и разного действия. Чаще всего это
 * вылезает как кнопка «создать» на пустом результате поиска.
 *
 * Новый код берёт из `@/components/ui`: EmptyFirstRun, EmptyFilter,
 * EmptyNoAccess, EmptyFailure. Этот компонент остаётся, пока на него ссылаются
 * старые экраны (14 мест на 6 страницах), и удаляется вместе с их переделкой.
 */
interface Props {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: Props) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <div className="grid h-10 w-10 place-items-center rounded-lg bg-bg-subtle text-fg-subtle">
        <Icon className="h-5 w-5" aria-hidden />
      </div>
      <div>
        <div className="text-sm font-medium text-fg">{title}</div>
        {description && <div className="mt-1 text-xs text-fg-muted">{description}</div>}
      </div>
      {action}
    </div>
  );
}
