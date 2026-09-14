import { type LucideIcon } from "lucide-react";

interface Props {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: Props) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-bg-elevated text-fg-subtle ring-1 ring-border">
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <div className="text-sm font-medium text-fg">{title}</div>
        {description && <div className="mt-1 text-xs text-fg-muted">{description}</div>}
      </div>
      {action}
    </div>
  );
}
