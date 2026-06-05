import { useEffect, useState } from "react";
import { useEventStream } from "@/lib/ws";

export function LiveIndicator() {
  const [status, setStatus] = useState<"connecting" | "live" | "offline">("connecting");
  const [lastBeat, setLastBeat] = useState(Date.now());

  useEventStream(() => {
    setStatus("live");
    setLastBeat(Date.now());
  });

  // Treat as offline if no ping/event in the last 60 seconds
  // (server pings every 25s).
  useEffect(() => {
    const t = window.setInterval(() => {
      if (Date.now() - lastBeat > 60000) setStatus("offline");
    }, 5000);
    return () => window.clearInterval(t);
  }, [lastBeat]);

  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-40 hidden items-center gap-2 rounded-full border border-border bg-bg-card/80 px-3 py-1.5 text-[11px] font-medium backdrop-blur md:flex">
      <span
        className={
          status === "live"
            ? "h-1.5 w-1.5 rounded-full bg-success animate-pulse-glow"
            : status === "offline"
            ? "h-1.5 w-1.5 rounded-full bg-danger"
            : "h-1.5 w-1.5 rounded-full bg-warning"
        }
      />
      <span className="text-fg-muted">
        {status === "live" ? "Live" : status === "offline" ? "Offline" : "..."}
      </span>
    </div>
  );
}
