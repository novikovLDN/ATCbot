/**
 * Remnawave 3.x backfill — caches numeric panel ids and writes telegramId
 * into the panel for users created before the 2.7.4 → 3.x upgrade.
 * Moved here unchanged in behaviour from the legacy home screen.
 */
import { useEffect, useState } from "react";
import { endpoints, type RemnawaveBackfillStatus } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { Surface } from "@/components/ui/Surface";
import { ListRow, PillProgress, StatusDot } from "@/components/ui/controls";

export function RemnawaveBackfillCard({ className }: { className?: string }) {
  const [status, setStatus] = useState<RemnawaveBackfillStatus | null>(null);
  const [busy, setBusy] = useState(false);

  // Poll every 2 s while the job runs.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await endpoints.remnawaveBackfillStatus();
        if (!cancelled) setStatus(s);
      } catch {
        // silent
      }
    };
    tick();
    const iv = setInterval(() => {
      if (status?.running) tick();
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [status?.running]);

  const start = async (dry: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await endpoints.remnawaveBackfillStart(dry);
      setStatus(r.status);
      if (!r.ok) toast.info("Уже запущено — прогресс ниже");
      else toast.success(dry ? "Пробный прогон запущен" : "Синхронизация запущена");
    } catch (e) {
      toast.error("Не удалось запустить: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const s = status;
  const pct = s && s.total > 0 ? Math.round((s.processed / s.total) * 100) : null;

  return (
    <Surface
      className={className}
      variant="raised"
      label="Синхронизация id с панелью"
      aside={
        <div className="flex gap-2">
          <button type="button" className="btn-secondary px-3 py-1.5 text-[12px]" onClick={() => start(true)} disabled={busy || s?.running}>
            Пробный прогон
          </button>
          <button type="button" className="btn-primary px-3 py-1.5 text-[12px]" onClick={() => start(false)} disabled={busy || s?.running}>
            Запустить
          </button>
        </div>
      }
    >
      <p className="t-mute mb-4 max-w-[70ch] text-[13px] leading-5">
        Нужна один раз для пользователей, созданных до перехода на Remnawave 3.x. Пробный прогон ничего не записывает.
      </p>
      {pct !== null && (
        <div className="mb-4">
          <PillProgress
            value={pct}
            label={s?.running ? "Идёт" : s?.finished_at ? "Готово" : "Остановлено"}
            valueLabel={`${pct}% за ${s?.elapsed_sec.toFixed(0)} с${s?.dry_run ? ", пробный" : ""}`}
          />
        </div>
      )}
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        <li><ListRow title="Всего" value={fmtNum(s?.total ?? 0)} /></li>
        <li><ListRow title="Обработано" value={fmtNum(s?.processed ?? 0)} /></li>
        <li><ListRow title="id закешировано" value={fmtNum(s?.id_backfilled ?? 0)} /></li>
        <li><ListRow title="telegramId записан в панель" value={fmtNum(s?.tg_backfilled ?? 0)} /></li>
        <li><ListRow title="Не найдены в панели" value={fmtNum(s?.missing ?? 0)} /></li>
        <li>
          <ListRow
            leading={(s?.errors ?? 0) > 0 ? <StatusDot tone="err" /> : undefined}
            title="Ошибок"
            value={fmtNum(s?.errors ?? 0)}
          />
        </li>
      </ul>
      {s?.last_error && (
        <div role="alert" className="mt-3 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
          <StatusDot tone="err" />
          <span className="min-w-0 truncate">{s.last_error}</span>
        </div>
      )}
    </Surface>
  );
}
