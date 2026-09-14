import { Construction } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";

export function ComingSoon({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          В разработке
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          {title}
        </h1>
      </header>
      <EmptyState
        icon={Construction}
        title="Раздел в работе"
        description={
          subtitle ??
          "Появится в одной из следующих фаз. Сейчас доступно через in-bot админ-меню."
        }
      />
    </div>
  );
}
