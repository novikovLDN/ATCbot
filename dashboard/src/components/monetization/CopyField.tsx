import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Строка со ссылкой и кнопкой «скопировать».
 *
 * ССЫЛКА ВСЕГДА ПРИХОДИТ С СЕРВЕРА. Имя бота живёт в config, и склеенный
 * в разметке диплинк рано или поздно расходится с настоящим — так на
 * экране подарочных ГБ два года лежала ссылка на другого бота, и понять
 * это можно было, только попробовав перейти. Компонент нарочно не умеет
 * собирать ссылку сам: ему дают готовую.
 *
 * ПОДТВЕРЖДЕНИЕ КОПИРОВАНИЯ — НА МЕСТЕ, А НЕ ТОСТОМ В УГЛУ. Действие
 * мгновенное и безобидное, обратная связь нужна там, куда человек
 * смотрел (NN/g: реакция на месте источника). Галочка живёт полторы
 * секунды и не отнимает фокус.
 */
export function CopyField({
  value,
  label,
  className,
}: {
  value: string;
  /** Что копируется — озвучивается скринридеру на кнопке. */
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Буфер обмена может быть недоступен (нет https, отказ в правах).
      // Молчать нельзя: человек решит, что скопировалось.
      setCopied(false);
      window.prompt("Скопируйте ссылку вручную:", value);
    }
  };

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border border-border bg-bg-subtle px-2 py-1.5",
        className,
      )}
    >
      <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg">{value}</code>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? `${label} скопирована` : `Скопировать ${label}`}
        className="inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-fg-muted transition-colors hover:bg-bg-card hover:text-fg"
      >
        {copied ? (
          <>
            <Check className="h-3 w-3" aria-hidden />
            Скопировано
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" aria-hidden />
            Копировать
          </>
        )}
      </button>
    </div>
  );
}
