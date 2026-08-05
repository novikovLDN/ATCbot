import { useEffect, useState } from "react";
import { Wifi, WifiOff, Loader2 } from "lucide-react";
import { useEventStream } from "@/lib/ws";

/**
 * Индикатор живого соединения.
 *
 * Было: плавающая плашка, приклеенная к правому нижнему углу поверх контента,
 * где состояние различалось по сути только цветом точки (WCAG 1.4.1). Стало:
 * элемент шапки — цвет, значок и слово сразу.
 *
 * role="status", а не "alert": обрыв связи с шиной событий не требует
 * немедленного вмешательства и не должен перебивать то, что человек читает.
 */
export function LiveIndicator() {
  const [status, setStatus] = useState<"connecting" | "live" | "offline">("connecting");
  const [lastBeat, setLastBeat] = useState(Date.now());

  useEventStream(() => {
    setStatus("live");
    setLastBeat(Date.now());
  });

  // Считаем связь потерянной, если 60 секунд не было ни события, ни пинга
  // (сервер пингует каждые 25 секунд).
  useEffect(() => {
    const t = window.setInterval(() => {
      if (Date.now() - lastBeat > 60000) setStatus("offline");
    }, 5000);
    return () => window.clearInterval(t);
  }, [lastBeat]);

  const map = {
    live: { Icon: Wifi, word: "Связь есть", cls: "text-success" },
    offline: { Icon: WifiOff, word: "Нет связи", cls: "text-danger" },
    connecting: { Icon: Loader2, word: "Подключаюсь", cls: "text-fg-muted" },
  } as const;
  const { Icon, word, cls } = map[status];

  return (
    <div
      role="status"
      // На узком экране остаётся один значок: слово прячем визуально, но из
      // подписи для скринридера оно никуда не девается.
      className={`inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium ${cls}`}
      title={word}
    >
      <Icon
        className={`h-3.5 w-3.5 shrink-0 ${status === "connecting" ? "animate-spin" : ""}`}
        aria-hidden
      />
      <span className="hidden lg:inline">{word}</span>
      <span className="sr-only lg:hidden">{word}</span>
    </div>
  );
}
