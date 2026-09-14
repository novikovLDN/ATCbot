/**
 * Premium expireAt > 5 years — Settings → «Премиум больше 5 лет».
 *
 * «Проверить» starts a background dry run on the server (the panel stream +
 * the repair rule of app/services/premium_repair): nothing is changed; the
 * card shows the counts and the users (table + CSV). «Исправить» starts ONE
 * background repair: PATCH expireAt of the premium entity only, ≤ 2 per
 * second, each user re-read from the DB right before, a date is never
 * extended. Progress lives in the server's settings storage, so the card
 * shows the same numbers after a reload or a bot restart. Pause / resume / stop.
 */
import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  endpoints,
  type PremiumRepairActionResult,
  type PremiumRepairCheck,
  type PremiumRepairRow,
  type PremiumRepairState,
  type PremiumRepairStatus,
} from "@/lib/api";
import { fmtDate, fmtDuration, fmtNum } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { Surface } from "@/components/ui/Surface";
import { ConfirmSheet } from "@/components/ui/ConfirmSheet";
import { ListRow, PillProgress, StatusDot, type Tone } from "@/components/ui/controls";
import { ErrorState } from "@/components/ui/states";
import { toast } from "@/store/toast";

const STATE_LABEL: Record<PremiumRepairState, string> = {
  idle: "Не запускалось",
  running: "Исправляю",
  paused: "На паузе",
  interrupted: "Прервано перезапуском бота",
  stopped: "Остановлено",
  done: "Готово",
  failed: "Ошибка",
};
const STATE_TONE: Record<PremiumRepairState, Tone> = {
  idle: "idle",
  running: "info",
  paused: "warn",
  interrupted: "warn",
  stopped: "idle",
  done: "ok",
  failed: "err",
};

const SOURCE_LABEL: Record<string, string> = { purchases: "по покупкам", db: "по БД", fallback: "завтра" };
const FALLBACK_LABEL: Record<string, string> = { no_payments: "нет покупок", past_date: "дата в прошлом" };
const REASON_LABEL: Record<string, string> = {
  would_extend: "покупки не раньше даты в панели",
  no_panel_id: "нет id в панели",
  limit: "за пределом пробного запуска",
  not_a_premium_entity: "не premium-сущность",
  panel_patch_rejected: "панель отклонила",
};
const ACTION_LABEL: Record<PremiumRepairRow["action"], string> = {
  would_fix: "исправить",
  fixed: "исправлено",
  skip: "пропуск",
  error: "ошибка",
};
const ACTION_BADGE: Record<PremiumRepairRow["action"], string> = {
  would_fix: "badge-accent",
  fixed: "badge-success",
  skip: "badge-muted",
  error: "badge-danger",
};

const ERROR_TEXT: Record<string, string> = {
  already_running: "Уже идёт — прогресс ниже",
  not_running: "Сейчас ничего не идёт",
  not_resumable: "Продолжать нечего — запустите заново",
  panel_unavailable: "Панель не ответила — попробуйте позже",
  db_unavailable: "База данных недоступна",
  no_report: "Отчёта пока нет — сначала проверка",
};

function errText(e: unknown, fallback: string): string {
  const d = (e as ApiError)?.detail;
  return (d && ERROR_TEXT[d]) || d || fallback;
}

function reasonText(r: string | null): string {
  if (!r) return "";
  return REASON_LABEL[r] ?? r;
}

function sourceText(r: PremiumRepairRow): string {
  const src = (r.target_source && SOURCE_LABEL[r.target_source]) || "—";
  return r.fallback ? `${src} (${FALLBACK_LABEL[r.fallback] ?? r.fallback})` : src;
}

/** Date only, with the year — the panel dates here run to 2036. */
function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function fmtEta(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  if (sec < 60) return "< 1 мин";
  return `≈ ${fmtDuration(sec)}`;
}

/** "label N · label N" — `none` (e.g. rows without a fallback) is not a reason. */
function breakdown(rec: Record<string, number> | undefined, labels: Record<string, string>): string {
  return Object.entries(rec ?? {})
    .filter(([k, n]) => k !== "none" && n > 0)
    .map(([k, n]) => `${labels[k] ?? k} ${fmtNum(n)}`)
    .join(" · ");
}

function headline(s: PremiumRepairStatus): { tone: Tone; label: string } {
  const a = s.apply.state;
  if (s.running_kind === "check") return { tone: "info", label: "Идёт проверка" };
  if (a === "running" || a === "paused" || a === "interrupted") return { tone: STATE_TONE[a], label: STATE_LABEL[a] };
  if (s.check.state === "done" && !s.check.stale) return { tone: "ok", label: "Проверено" };
  if (a !== "idle") return { tone: STATE_TONE[a], label: STATE_LABEL[a] };
  if (s.check.state === "failed") return { tone: "err", label: "Проверка не удалась" };
  if (s.check.state === "interrupted") return { tone: "warn", label: "Проверка прервана" };
  return { tone: "idle", label: "Не запускалось" };
}

const STATUS_KEY = ["premium-repair", "status"] as const;
const PREVIEW_ROWS = 20;
const MAX_ROWS = 10_000;

export function PremiumRepairCard({ className }: { className?: string }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<"start" | "stop" | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [limit, setLimit] = useState("");
  const limitId = useId();

  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: endpoints.premiumRepairStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  });
  const s = status.data;
  const report = s?.report ?? null;

  // A new report (check or repair) has a new generated_at → a fresh fetch.
  const rows = useQuery({
    queryKey: ["premium-repair", "report", report?.generated_at ?? null, showAll],
    queryFn: () => endpoints.premiumRepairReport(showAll ? Math.min(Math.max(report?.rows ?? 1, 1), MAX_ROWS) : PREVIEW_ROWS),
    enabled: report != null,
    staleTime: Infinity,
  });

  const onAction = (okText: string) => ({
    onSuccess: (r: PremiumRepairActionResult) => {
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

  const check = useMutation({ mutationFn: endpoints.premiumRepairCheck, ...onAction("Проверка запущена") });
  const start = useMutation({
    mutationFn: (n: number | null) => endpoints.premiumRepairStart(n),
    ...onAction("Запущено: даты исправляются"),
  });
  const pause = useMutation({ mutationFn: endpoints.premiumRepairPause, ...onAction("Пауза") });
  const resume = useMutation({ mutationFn: endpoints.premiumRepairResume, ...onAction("Продолжаю") });
  const stop = useMutation({ mutationFn: endpoints.premiumRepairStop, ...onAction("Остановлено") });
  const csv = useMutation({
    mutationFn: () => endpoints.premiumRepairCsv(),
    onError: (e: unknown) => toast.error(errText(e, "Не удалось скачать CSV")),
  });
  const busy = check.isPending || start.isPending || pause.isPending || resume.isPending || stop.isPending;

  const c = s?.check;
  const a = s?.apply;
  const applyState = a?.state ?? "idle";
  const active = applyState === "running" || applyState === "paused" || applyState === "interrupted";
  const finished = applyState === "done" || applyState === "stopped" || applyState === "failed";
  const checking = s?.running_kind === "check";
  const checkReady = !!c && c.state === "done" && !c.stale && c.summary != null;
  const nothingToFix = checkReady && c.would_fix === 0;
  const showApply = !!a && (active || (finished && !!c?.stale));
  // The run first re-reads the whole panel (a minute or two): no «0 из 0» and no
  // «Пауза» then — a second tap used to pause it before the first fix.
  const scanning = applyState === "running" && a?.phase === "scan";
  const pct = a && a.total > 0 ? Math.round((a.done / a.total) * 100) : a && active ? 0 : null;

  const limitNum = limit.trim() === "" ? null : Number(limit);
  const limitInvalid = limitNum !== null && (!Number.isInteger(limitNum) || limitNum < 1);
  const head = s ? headline(s) : null;

  return (
    <Surface
      className={className}
      label="Премиум больше 5 лет"
      aside={
        head ? (
          <span className="inline-flex items-center gap-1.5 text-[13px]">
            <StatusDot tone={head.tone} />
            <span className="t-mute">{head.label}</span>
          </span>
        ) : undefined
      }
    >
      <p className="t-mute mb-4 max-w-[72ch] text-[13px] leading-5">
        Premium-сущности <span className="font-mono">tg_&lt;id&gt;_premium</span>, у которых срок в панели дальше чем на
        5 лет вперёд. Дата ставится по реальным покупкам (купил год — год), если покупок нет или дата в прошлом —
        завтра. Меняется только срок premium-сущности: bypass, трафик и статус не трогаются, дата никогда не
        продлевается. Не больше {s?.rate_per_sec ?? 2} изменений в секунду.
      </p>

      {status.isError && (
        <ErrorState className="mb-3 rounded-row bg-tile-3 p-4" error={status.error} onRetry={() => status.refetch()} />
      )}

      {/* ── Check (dry run) ─────────────────────────────────────── */}
      {checking && (
        <div className="mb-4 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
          <Spinner />
          <span className="min-w-0">Проверяю панель и покупки — это может занять пару минут. Ничего не меняется.</span>
        </div>
      )}
      {c && c.state === "failed" && !checking && (
        <div role="alert" className="mb-4 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
          <StatusDot tone="err" />
          <span className="min-w-0 break-words">Проверка не удалась: {errText({ detail: c.last_error }, "ошибка")}</span>
        </div>
      )}
      {c && c.state === "interrupted" && !checking && (
        <p className="t-mute mb-4 text-[13px] leading-5">Проверка прервана перезапуском бота — запустите её снова.</p>
      )}
      {checkReady && !active && c.summary && <CheckSummary check={c} />}

      {/* ── Repair progress ─────────────────────────────────────── */}
      {showApply && a && (
        <div className="mb-4">
          {scanning && (
            <div className="mb-3 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
              <Spinner />
              <span className="min-w-0">
                Читаю панель — это займёт 1–2 минуты, затем начнётся исправление. Ничего не нажимайте.
              </span>
            </div>
          )}
          {pct !== null && !scanning && (
            <div className="mb-3">
              <PillProgress
                value={pct}
                label={STATE_LABEL[applyState]}
                valueLabel={`${fmtNum(a.done)} из ${fmtNum(a.total)} · ${pct}%`}
              />
            </div>
          )}
          <ul className="flex flex-col">
            <li><ListRow title="Исправлено" value={fmtNum(a.fixed)} /></li>
            <li>
              <ListRow
                leading={a.errors > 0 ? <StatusDot tone="err" /> : undefined}
                title="Ошибок"
                meta={a.errors > 0 ? "каждая записана в лог, задача не прерывается" : undefined}
                value={fmtNum(a.errors)}
              />
            </li>
            {a.skipped > 0 && (
              <li>
                <ListRow title="Пропущено" meta="по свежим данным дата не раньше, чем в панели" value={fmtNum(a.skipped)} />
              </li>
            )}
            {a.remaining > 0 && applyState !== "running" && (
              <li><ListRow title="Не обработано" value={fmtNum(a.remaining)} /></li>
            )}
            <li><ListRow title="На «завтра»" meta="нет покупок или дата в прошлом" value={fmtNum(a.plus_one_day)} /></li>
            <li><ListRow title="Подрезано дат в БД" value={fmtNum(a.db_shortened)} /></li>
            {a.limit != null && <li><ListRow title="Пробный запуск" value={`первые ${fmtNum(a.limit)}`} /></li>}
            {a.started_at && <li><ListRow title="Запущено" value={fmtDate(a.started_at)} /></li>}
            {finished && a.finished_at && <li><ListRow title="Завершено" value={fmtDate(a.finished_at)} /></li>}
          </ul>
          {a.last_error && (
            <div role="alert" className="mt-3 flex items-center gap-3 rounded-row bg-tile-3 px-4 py-3 text-[13px]">
              <StatusDot tone="err" />
              <span className="min-w-0 break-words">{errText({ detail: a.last_error }, a.last_error)}</span>
            </div>
          )}
          {applyState === "interrupted" && (
            <p className="t-mute mt-3 text-[13px] leading-5">
              Бот перезапускался. «Продолжить» заново просканирует панель — уже исправленные в выборку не попадут.
            </p>
          )}
          {applyState === "paused" && a.limit != null && a.remaining > 0 && (
            <p className="t-mute mt-3 text-[13px] leading-5">
              Пробный запуск закончен. Проверьте результат в таблице ниже, затем «Продолжить» — для остальных.
            </p>
          )}
        </div>
      )}

      {/* ── Actions ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2">
        {!active && (
          <>
            <button type="button" className="btn-secondary" onClick={() => check.mutate()} disabled={busy || checking || !s}>
              {(checking || check.isPending) && <Spinner />}
              {checking ? "Проверяю…" : checkReady ? "Обновить проверку" : "Проверить"}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                setLimit("");
                setConfirm("start");
              }}
              disabled={busy || checking || !checkReady || nothingToFix}
            >
              Исправить
            </button>
          </>
        )}
        {applyState === "running" && !scanning && (
          <button type="button" className="btn-secondary" onClick={() => pause.mutate()} disabled={busy}>
            {pause.isPending && <Spinner />}
            Пауза
          </button>
        )}
        {(applyState === "paused" || applyState === "interrupted") && (
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
      {!active && !checking && !checkReady && (
        <p className="t-mute mt-2 text-[13px]">«Исправить» станет доступна после проверки.</p>
      )}
      {nothingToFix && !active && (
        <p className="t-mute mt-2 text-[13px]">Исправлять нечего — premium-сущностей больше 5 лет к исправлению нет.</p>
      )}

      {/* ── Users (the last report) ─────────────────────────────── */}
      {report && !checking && (
        <div className="mt-6">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="t-mute text-[13px]">
              {report.kind === "apply" ? "Итог исправления" : "Проверка"} на {fmtDate(report.generated_at)} ·{" "}
              {fmtNum(report.rows)} польз.
            </h3>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => csv.mutate()}
              disabled={csv.isPending || report.rows === 0}
            >
              {csv.isPending && <Spinner />}
              Скачать CSV
            </button>
          </div>
          {rows.isError && (
            <ErrorState className="rounded-row bg-tile-3 p-4" error={rows.error} onRetry={() => rows.refetch()} />
          )}
          {rows.isLoading && <p className="t-mute text-[13px]">Загружаю…</p>}
          {rows.data && rows.data.rows.length === 0 && (
            <p className="t-mute text-[13px]">Premium-сущностей больше 5 лет нет.</p>
          )}
          {rows.data && rows.data.rows.length > 0 && <ReportRows rows={rows.data.rows} />}
          {report.rows > PREVIEW_ROWS && (
            <button type="button" className="btn-ghost mt-2" onClick={() => setShowAll((v) => !v)} disabled={rows.isFetching}>
              {rows.isFetching && <Spinner />}
              {showAll ? `Показать первые ${PREVIEW_ROWS}` : `Показать все (${fmtNum(report.rows)})`}
            </button>
          )}
        </div>
      )}

      {confirm === "start" && c && (
        <ConfirmSheet
          title="Исправить даты?"
          confirmLabel="Исправить"
          pending={start.isPending}
          confirmDisabled={limitInvalid}
          onCancel={() => setConfirm(null)}
          onConfirm={() => start.mutate(limitNum)}
        >
          <StartSummary check={c} rate={s?.rate_per_sec ?? 2} />
          <label htmlFor={limitId} className="t-mute mb-1 mt-4 block text-[13px]">
            Только первые N — пробный запуск <span className="text-ash">(необязательно)</span>
          </label>
          <input
            id={limitId}
            className="input tabular"
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            placeholder={`все ${fmtNum(c.would_fix)}`}
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            aria-invalid={limitInvalid || undefined}
          />
          {limitInvalid && <p className="mt-1 text-[13px] text-danger">Целое число от 1</p>}
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
          Уже исправленные даты останутся. Остальных можно исправить позже новым запуском — исправленные в выборку
          больше не попадут. Придёт сообщение в Telegram с итогом.
        </ConfirmSheet>
      )}
    </Surface>
  );
}

function CheckSummary({ check }: { check: PremiumRepairCheck }) {
  const sm = check.summary!;
  const skip = sm.actions.skip ?? 0;
  const src = sm.target_source;
  return (
    <div className="mb-4">
      <h3 className="t-mute mb-1 text-[13px]">
        Проверка на {fmtDate(check.finished_at)} · ничего не изменено
      </h3>
      <ul className="flex flex-col">
        <li><ListRow title="Сущностей в панели" meta={`из них premium ${fmtNum(sm.premium_entities)}`} value={fmtNum(sm.panel_entities)} /></li>
        <li>
          <ListRow
            leading={<StatusDot tone={sm.candidates > 0 ? "warn" : "ok"} />}
            title="Premium больше 5 лет"
            value={fmtNum(sm.candidates)}
          />
        </li>
        <li><ListRow title="Будет исправлено" value={fmtNum(check.would_fix)} /></li>
        {skip > 0 && (
          <li>
            <ListRow title="Будет пропущено" meta={breakdown(sm.skip_reasons, REASON_LABEL)} value={fmtNum(skip)} />
          </li>
        )}
        <li><ListRow title="Дата по покупкам" value={fmtNum(src.purchases ?? 0)} /></li>
        <li><ListRow title="Дата по БД" meta="баланс, подарки, промо — чего нет в платежах" value={fmtNum(src.db ?? 0)} /></li>
        <li>
          <ListRow
            title="На «завтра» (+1 день)"
            meta={breakdown(sm.fallback, FALLBACK_LABEL) || "нет покупок или дата в прошлом"}
            value={fmtNum(sm.plus_one_day)}
          />
        </li>
        <li>
          <ListRow title="Подрезать дат в БД" meta="больше 5 лет, не bypass-only" value={fmtNum(sm.db_leaked)} />
        </li>
        <li><ListRow title="Займёт" value={fmtEta(check.eta_seconds)} /></li>
      </ul>
    </div>
  );
}

function StartSummary({ check, rate }: { check: PremiumRepairCheck; rate: number }) {
  const sm = check.summary;
  if (!sm) return null;
  const src = sm.target_source;
  return (
    <>
      Будет исправлено <span className="tabular font-semibold">{fmtNum(check.would_fix)}</span> premium-сущностей (по
      проверке на {fmtDate(check.finished_at)}): {fmtNum(src.purchases ?? 0)} по покупкам, {fmtNum(src.db ?? 0)} по БД,{" "}
      {fmtNum(sm.plus_one_day)} на «завтра». Подрезать дат в БД: {fmtNum(sm.db_leaked)}. Займёт{" "}
      {fmtEta(check.eta_seconds)}, не больше {rate} в секунду. Перед каждым изменением данные пользователя
      перечитываются из БД. По завершении придёт сообщение в Telegram.
    </>
  );
}

function ReportRows({ rows }: { rows: PremiumRepairRow[] }) {
  return (
    <>
      {/* ── Phone: cards ─────────────────────────────────────── */}
      <ul className="flex flex-col gap-2 md:hidden">
        {rows.map((r, i) => (
          <li key={`${r.telegram_id}-${i}`} className="rounded-row bg-tile-3 px-4 py-2.5">
            <div className="flex items-center justify-between gap-3">
              <span className="tabular min-w-0 truncate text-[15px] font-medium">{r.telegram_id}</span>
              <span className={ACTION_BADGE[r.action]}>{ACTION_LABEL[r.action]}</span>
            </div>
            <div className="tabular mt-1 text-[13px]">
              <span className="t-mute">{fmtDay(r.panel_expire_at)}</span> → <span className="font-medium">{fmtDay(r.target)}</span>
            </div>
            <div className="t-mute mt-0.5 text-[12px] leading-4">
              {sourceText(r)}
              {r.db_leaked ? " · дата в БД > 5 лет" : ""}
              {r.reason ? ` · ${reasonText(r.reason)}` : ""}
            </div>
          </li>
        ))}
      </ul>

      {/* ── Desktop: table ───────────────────────────────────── */}
      <div className="-mx-2 hidden overflow-x-auto px-2 md:block">
        <table className="dtable min-w-[640px]">
          <caption className="sr-only">
            Premium-сущности со сроком больше 5 лет: Telegram ID, дата в панели, новая дата, откуда она взята, действие.
          </caption>
          <thead>
            <tr>
              <th scope="col">Telegram ID</th>
              <th scope="col">В панели</th>
              <th scope="col">Станет</th>
              <th scope="col">Откуда дата</th>
              <th scope="col">Действие</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.telegram_id}-${i}`}>
                <td className="tabular">{r.telegram_id}</td>
                <td className="t-mute tabular whitespace-nowrap">{fmtDay(r.panel_expire_at)}</td>
                <td className="tabular whitespace-nowrap font-medium">{fmtDay(r.target)}</td>
                <td>
                  {sourceText(r)}
                  {r.db_leaked && <span className="badge-warning ml-2">БД &gt; 5 лет</span>}
                </td>
                <td>
                  <span className={ACTION_BADGE[r.action]}>{ACTION_LABEL[r.action]}</span>
                  {r.reason && <span className="t-mute ml-2 text-[12px]">{reasonText(r.reason)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
