/**
 * Remnawave user tags by tariff — Settings → «Теги в панели Remnawave».
 *
 * Preview is a dry run (GET /remnawave-tags/preview): how many entities of
 * users with an active subscription would get which tag. «Проставить теги»
 * starts ONE background job on the server (tag-only PATCH, ≤ 2 per second);
 * its progress lives in the server's settings storage, so this card shows
 * the same numbers after a reload or a bot restart. Pause / resume / stop.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  endpoints,
  type RemnawaveTag,
  type RemnawaveTagsActionResult,
  type RemnawaveTagsPreview,
  type RemnawaveTagsState,
  type RemnawaveTagsStatus,
} from "@/lib/api";
import { fmtDate, fmtDuration, fmtNum } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { Surface } from "@/components/ui/Surface";
import { ListRow, PillProgress, StatusDot, type Tone } from "@/components/ui/controls";
import { ErrorState } from "@/components/ui/states";
import { toast } from "@/store/toast";

const TAG_LABEL: Record<RemnawaveTag, string> = {
  TRIAL: "Пробный",
  BASIC: "Basic",
  PLUS: "Plus",
  COMBO_BASIC: "Combo Basic",
  COMBO_PLUS: "Combo Plus",
  BYPASS: "Обход",
};
const TAG_ORDER: RemnawaveTag[] = ["TRIAL", "BASIC", "PLUS", "COMBO_BASIC", "COMBO_PLUS", "BYPASS"];

const STATE_LABEL: Record<RemnawaveTagsState, string> = {
  idle: "Не запускалось",
  running: "Идёт",
  paused: "На паузе",
  interrupted: "Прервано перезапуском бота",
  stopped: "Остановлено",
  done: "Готово",
  failed: "Ошибка",
};
const STATE_TONE: Record<RemnawaveTagsState, Tone> = {
  idle: "idle",
  running: "info",
  paused: "warn",
  interrupted: "warn",
  stopped: "idle",
  done: "ok",
  failed: "err",
};

const ERROR_TEXT: Record<string, string> = {
  already_running: "Уже идёт — прогресс ниже",
  not_running: "Сейчас ничего не идёт",
  not_resumable: "Продолжать нечего — запустите заново",
};

function errText(e: unknown, fallback: string): string {
  const d = (e as ApiError)?.detail;
  return (d && ERROR_TEXT[d]) || d || fallback;
}

function fmtEta(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  if (sec < 60) return "< 1 мин";
  return `≈ ${fmtDuration(sec)}`;
}

const STATUS_KEY = ["remnawave-tags", "status"] as const;
const PREVIEW_KEY = ["remnawave-tags", "preview"] as const;

export function RemnawaveTagsCard({ className }: { className?: string }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<"start" | "stop" | null>(null);

  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: endpoints.remnawaveTagsStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  });

  // Dry run on demand only: it streams the whole panel.
  const preview = useQuery({
    queryKey: PREVIEW_KEY,
    queryFn: endpoints.remnawaveTagsPreview,
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });

  const onAction = (okText: string) => ({
    onSuccess: (r: RemnawaveTagsActionResult) => {
      qc.setQueryData(STATUS_KEY, r.status);
      setConfirm(null);
      if (r.ok) toast.success(okText);
      else toast.info("Состояние уже изменилось — показываю текущее");
    },
    onError: (e: unknown) => {
      setConfirm(null);
      toast.error(errText(e, "Не удалось выполнить"));
      status.refetch();
    },
  });

  const start = useMutation({ mutationFn: endpoints.remnawaveTagsStart, ...onAction("Запущено: теги проставляются") });
  const pause = useMutation({ mutationFn: endpoints.remnawaveTagsPause, ...onAction("Пауза") });
  const resume = useMutation({ mutationFn: endpoints.remnawaveTagsResume, ...onAction("Продолжаю") });
  const stop = useMutation({ mutationFn: endpoints.remnawaveTagsStop, ...onAction("Остановлено") });
  const busy = start.isPending || pause.isPending || resume.isPending || stop.isPending;

  // A finished run changes the panel: the old preview no longer holds.
  const lastState = useRef<RemnawaveTagsState | undefined>(undefined);
  useEffect(() => {
    const st = status.data?.state;
    if (lastState.current === "running" && st && st !== "running") {
      qc.removeQueries({ queryKey: PREVIEW_KEY });
    }
    lastState.current = st;
  }, [status.data?.state, qc]);

  const s = status.data;
  const state = s?.state ?? "idle";
  const active = state === "running" || state === "paused" || state === "interrupted";
  const finished = state === "done" || state === "stopped" || state === "failed";
  const p = preview.data;
  const nothingToDo = p != null && p.differ === 0;
  const pct = s && s.total > 0 ? Math.round((s.done / s.total) * 100) : s && active ? 0 : null;

  return (
    <Surface
      className={className}
      label="Теги по тарифу"
      aside={
        s ? (
          <span className="inline-flex items-center gap-1.5 text-[13px]">
            <StatusDot tone={STATE_TONE[state]} />
            <span className="t-mute">{STATE_LABEL[state]}</span>
          </span>
        ) : undefined
      }
    >
      <p className="t-mute mb-4 max-w-[72ch] text-[13px] leading-5">
        Только пользователи с активной подпиской: premium-сущность получает тег тарифа (TRIAL, BASIC, PLUS,
        COMBO_BASIC, COMBO_PLUS), bypass-сущность — BYPASS. Меняется только тег — сроки, лимиты и статус не
        трогаются. Не больше {s?.rate_per_sec ?? 2} изменений в секунду; сущности с верным тегом пропускаются.
      </p>

      {status.isError && (
        <ErrorState className="mb-3 rounded-row bg-tile-3 p-4" error={status.error} onRetry={() => status.refetch()} />
      )}

      {/* ── Job progress ─────────────────────────────────────────── */}
      {s && (active || finished) && (
        <div className="mb-4">
          {pct !== null && (
            <div className="mb-3">
              <PillProgress
                value={pct}
                label={STATE_LABEL[state]}
                valueLabel={`${fmtNum(s.done)} из ${fmtNum(s.total)} · ${pct}%`}
              />
            </div>
          )}
          <ul className="flex flex-col">
            <li><ListRow title="Изменено" value={fmtNum(s.patched)} /></li>
            <li>
              <ListRow
                leading={s.errors > 0 ? <StatusDot tone="err" /> : undefined}
                title="Ошибок"
                meta={s.errors > 0 ? "каждая записана в лог, задача не прерывается" : undefined}
                value={fmtNum(s.errors)}
              />
            </li>
            <li><ListRow title="Всего к изменению" value={fmtNum(s.total)} /></li>
            {TAG_ORDER.filter((t) => (s.per_tag?.[t] ?? 0) > 0).map((t) => (
              <li key={t}>
                <ListRow title={TAG_LABEL[t]} meta={t} value={fmtNum(s.per_tag[t])} />
              </li>
            ))}
            {s.started_at && <li><ListRow title="Запущено" value={fmtDate(s.started_at)} /></li>}
            {finished && s.finished_at && <li><ListRow title="Завершено" value={fmtDate(s.finished_at)} /></li>}
          </ul>
          {s.last_error && (
            <div role="alert" className="mt-3 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
              <StatusDot tone="err" />
              <span className="min-w-0 break-words">{s.last_error}</span>
            </div>
          )}
          {state === "interrupted" && (
            <p className="t-mute mt-3 text-[13px] leading-5">
              Бот перезапускался. «Продолжить» доделает оставшееся — уже проставленные теги пропускаются.
            </p>
          )}
        </div>
      )}

      {/* ── Preview (dry run) ───────────────────────────────────── */}
      {preview.isError && (
        <ErrorState className="mb-3 rounded-row bg-tile-3 p-4" error={preview.error} onRetry={() => preview.refetch()} />
      )}
      {p && !active && (
        <div className="mb-4">
          <h3 className="t-mute mb-1 text-[13px]">
            Проверка на {fmtDate(p.generated_at)} · ничего не изменено
          </h3>
          <ul className="flex flex-col">
            {TAG_ORDER.map((t) => {
              const row = p.tags.find((x) => x.tag === t) ?? { total: 0, differ: 0 };
              return (
                <li key={t}>
                  <ListRow
                    leading={<StatusDot tone={row.differ > 0 ? "warn" : "ok"} />}
                    title={TAG_LABEL[t]}
                    meta={`${t} · к изменению`}
                    value={`${fmtNum(row.differ)} из ${fmtNum(row.total)}`}
                  />
                </li>
              );
            })}
            <li><ListRow title="Пользователей с подпиской" value={fmtNum(p.users)} /></li>
            <li><ListRow title="Будет изменено" value={fmtNum(p.differ)} /></li>
            <li><ListRow title="Тег уже верный" value={fmtNum(p.already)} /></li>
            <li>
              <ListRow
                leading={p.missing > 0 ? <StatusDot tone="warn" /> : undefined}
                title="Нет в панели"
                meta="такие сущности пропускаются"
                value={fmtNum(p.missing)}
              />
            </li>
            <li><ListRow title="Займёт" value={fmtEta(p.eta_seconds)} /></li>
          </ul>
        </div>
      )}

      {/* ── Actions ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2">
        {!active && (
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => preview.refetch()}
              disabled={preview.isFetching}
            >
              {preview.isFetching && <Spinner />}
              {p ? "Обновить проверку" : "Проверить"}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setConfirm("start")}
              disabled={busy || !s || nothingToDo || preview.isFetching}
            >
              Проставить теги
            </button>
          </>
        )}
        {state === "running" && (
          <button type="button" className="btn-secondary" onClick={() => pause.mutate()} disabled={busy}>
            {pause.isPending && <Spinner />}
            Пауза
          </button>
        )}
        {(state === "paused" || state === "interrupted") && (
          <button type="button" className="btn-primary" onClick={() => resume.mutate()} disabled={busy}>
            {resume.isPending && <Spinner />}
            Продолжить
          </button>
        )}
        {active && (
          <button type="button" className="btn-danger" onClick={() => setConfirm("stop")} disabled={busy}>
            Стоп
          </button>
        )}
      </div>
      {nothingToDo && !active && (
        <p className="t-mute mt-2 text-[13px]">Все теги уже верные — проставлять нечего.</p>
      )}

      {confirm === "start" && (
        <ConfirmSheet
          title="Проставить теги?"
          confirmLabel="Проставить"
          pending={start.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={() => start.mutate()}
        >
          <StartSummary preview={p} status={s} />
        </ConfirmSheet>
      )}
      {confirm === "stop" && (
        <ConfirmSheet
          title="Остановить?"
          confirmLabel="Остановить"
          danger
          pending={stop.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={() => stop.mutate()}
        >
          Уже проставленные теги останутся. Оставшиеся можно проставить позже новым запуском — верные теги он
          пропустит.
        </ConfirmSheet>
      )}
    </Surface>
  );
}

function StartSummary({ preview, status }: { preview?: RemnawaveTagsPreview; status?: RemnawaveTagsStatus }) {
  if (!preview) {
    return (
      <>
        Будут изменены все расходящиеся теги у пользователей с активной подпиской. Меняется только тег,
        не больше {status?.rate_per_sec ?? 2} изменений в секунду. По завершении придёт сообщение в Telegram.
      </>
    );
  }
  return (
    <>
      Будет изменено <span className="tabular font-semibold">{fmtNum(preview.differ)}</span> сущностей
      (по проверке на {fmtDate(preview.generated_at)}), займёт {fmtEta(preview.eta_seconds)}. Меняется только
      тег — сроки, лимиты и статус остаются. По завершении придёт сообщение в Telegram.
    </>
  );
}

/** iOS bottom sheet with Cancel / Confirm. Escape and the backdrop cancel. */
function ConfirmSheet({
  title,
  children,
  confirmLabel,
  danger,
  pending,
  onCancel,
  onConfirm,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // onCancel is a new function on every render (status polls every 2 s): keep
  // it in a ref so the effect runs once and never steals focus back.
  const cancelFn = useRef(onCancel);
  cancelFn.current = onCancel;
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && cancelFn.current();
    document.addEventListener("keydown", onKey);
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      html.style.overflow = prev;
    };
  }, []);

  return createPortal(
    <>
      <div className="sheet-backdrop" aria-hidden="true" onClick={onCancel} />
      <div role="dialog" aria-modal="true" aria-label={title} className="sheet">
        <div className="sheet-grabber" aria-hidden="true" />
        <div className="mx-auto max-w-[520px]">
          <h2 className="text-[20px] font-semibold leading-[25px]">{title}</h2>
          <div className="t-body mt-2 text-[17px] leading-[24px]">{children}</div>
          <div className="mt-5 grid grid-cols-2 gap-2">
            <button ref={cancelRef} type="button" className="btn-secondary w-full" onClick={onCancel}>
              Отмена
            </button>
            <button
              type="button"
              className={danger ? "btn-danger w-full" : "btn-primary w-full"}
              onClick={onConfirm}
              disabled={pending}
            >
              {pending && <Spinner />}
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
