/**
 * Loading and failure states for a card that lives inside a tile.
 *
 * ui/states' ErrorState is itself a tile, so nesting it inside a Surface
 * would stack two tiles of the same shade. These are the in-tile
 * equivalents: a row-shaped block on tile-3.
 *
 * A failed request and an empty result stay visibly different states —
 * see UserProfileStats for why that matters.
 */
import { Skeleton } from "@/components/ui/states";

export function CardLoading({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full rounded-row" />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function CardError({
  title,
  note,
  onRetry,
}: {
  title: string;
  note?: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="rounded-row bg-tile-3 p-4">
      <p className="flex items-center gap-2 text-[14px] font-medium">
        <span className="dot dot-err" aria-hidden="true" />
        {title}
      </p>
      {note && <p className="t-mute mt-1 text-[13px] leading-5">{note}</p>}
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary mt-3 bg-tile-4">
          Повторить
        </button>
      )}
    </div>
  );
}
