/**
 * Copy-to-clipboard for a single technical value.
 *
 * Support currently reads VPN keys and subscription URLs out of the
 * database by hand, because the screen rendered them as plain text — a
 * 60-character key selected with a mouse from a truncated cell is how
 * you end up pasting half of one. Every technical value on this screen
 * gets one of these next to it.
 *
 * `navigator.clipboard` is unavailable on insecure origins and can be
 * refused by permissions policy, so the failure path is explicit rather
 * than a promise nobody awaited: a rejected copy that silently does
 * nothing is worse than no button, since the operator walks away
 * believing they have the key.
 */
import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";

export interface CopyButtonProps {
  /** The value placed on the clipboard. Nullish renders nothing. */
  value: string | null | undefined;
  /** Names the value in the aria-label and the toast, e.g. "VPN-ключ". */
  label: string;
  className?: string;
}

export function CopyButton({ value, label, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  if (!value) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(value as string);
      setCopied(true);
      toast.success(`${label} скопирован`);
      // Purely a visual acknowledgement; the toast is the real one. Reset
      // is long enough to be seen and short enough that a second copy of
      // the same value still reads as a new action.
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      toast.error(`Не удалось скопировать: ${label}`);
    }
  }

  // Visually 28px so it sits inside a text row; the 44px hit area comes
  // from `tap-target`.
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`Скопировать: ${label}`}
      title={`Скопировать: ${label}`}
      className={cn(
        "tap-target inline-grid h-7 w-7 flex-none place-items-center rounded-full",
        "t-mute outline-none transition-colors duration-[var(--dur-instant)]",
        "hover:bg-tile-4 hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/45",
        className,
      )}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  );
}
