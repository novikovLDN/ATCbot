import { CheckCircle2, XCircle, Info, X } from "lucide-react";
import { useToasts } from "@/store/toast";
import { cn } from "@/lib/cn";

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
};

const STYLES = {
  success: "border-success/30 bg-success/10 text-success",
  error: "border-danger/30 bg-danger/10 text-danger",
  info: "border-accent/30 bg-accent/10 text-accent",
};

export function Toaster() {
  const items = useToasts((s) => s.items);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div className="pointer-events-none fixed right-4 top-[max(1rem,env(safe-area-inset-top))] z-[60] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      {items.map((t) => {
        const Icon = ICONS[t.kind];
        return (
          <div
            key={t.id}
            className={cn(
              "card pointer-events-auto flex items-start gap-3 p-3 pr-2 animate-slide-up",
              "border",
              STYLES[t.kind],
            )}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="flex-1 text-sm text-fg">{t.text}</div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="tap-target rounded-md p-1 text-fg-muted hover:bg-bg-elevated hover:text-fg"
              aria-label="Закрыть"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
