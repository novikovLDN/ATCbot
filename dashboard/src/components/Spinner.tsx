import { cn } from "@/lib/cn";

export function Spinner({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "h-4 w-4 animate-spin rounded-full border-2 border-fg-subtle/40 border-t-accent",
        className,
      )}
      role="status"
      aria-label="Loading"
    />
  );
}
