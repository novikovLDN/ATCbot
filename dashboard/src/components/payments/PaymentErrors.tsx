import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, ChevronRight, ExternalLink } from "lucide-react";

import { endpoints, type PaymentErrorRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRelative, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardHeader,
  EmptyFailure,
  LoadingGate,
  SkeletonTable,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
} from "@/components/ui";
import { providerLabel } from "./labels";
import { STEP_TITLES, isKnownStage, outcomeOf, stageMeta, type StageStep } from "./stages";

/**
 * Экран отказов оплаты — инструмент разбора жалобы «я заплатил, ничего
 * не пришло».
 *
 * ЧТО ОН ОБЯЗАН ПОКАЗЫВАТЬ, КРОМЕ КРАСНОЙ СТРОКИ
 *   1. СТАДИЮ: на каком из четырёх шагов пути платежа всё встало. Шаг
 *      сообщает главное — успели ли взять деньги до отказа.
 *   2. ИСХОД: чем кончилась покупка и есть ли доступ сейчас. Половина
 *      записей — это сорвавшиеся попытки, после которых человек оплатил
 *      со второго раза; звать по ним админа незачем.
 *   3. ЧТО ДЕЛАТЬ: конкретное следующее действие, а не «разобраться».
 *
 * «СБОЕВ НЕТ» ПИШЕТСЯ ТОЛЬКО ТОГДА, КОГДА МЫ ЭТО ПРОВЕРИЛИ. Старый экран
 * рисовал зелёное «Без сбоев» при упавшем запросе — самая опасная ложь
 * из возможных (аудит, первый раздел). Теперь у отказа загрузки своя
 * красная плашка, и слово «нет» в ней не встречается.
 */

export function PaymentErrors({ hours }: { hours: number }) {
  const [stage, setStage] = useState<string>("");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const summary = useQuery({
    queryKey: ["payments", "errors", "summary", hours],
    queryFn: () => endpoints.paymentsErrorsSummary(hours),
    refetchInterval: 60_000,
  });

  const list = useQuery({
    queryKey: ["payments", "errors", "list", hours, stage],
    queryFn: () =>
      endpoints.paymentsErrors({ hours, limit: 200, stage: stage || undefined }),
    refetchInterval: 60_000,
  });

  const rows: PaymentErrorRow[] = list.data ?? [];
  const selected = rows.find((r) => r.id === selectedId) ?? null;
  const needHands = rows.filter((r) => outcomeOf(r).needsHands).length;

  return (
    <div className="space-y-3">
      {/* Счётчики. Отдельный запрос — отдельная судьба при отказе: если
          не посчитались они, список всё равно показываем. */}
      {summary.isError ? (
        <div className="flex items-start gap-2 rounded-md border border-risk/40 bg-risk/[0.06] px-3 py-2.5">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-risk" aria-hidden />
          <div className="text-base text-fg">
            Счётчики отказов не посчитались. Сколько всего сбоев за период —
            неизвестно; список ниже мог прийти неполным.
            <button
              type="button"
              onClick={() => summary.refetch()}
              className="ml-2 text-accent-text underline-offset-2 hover:underline"
            >
              Повторить
            </button>
          </div>
        </div>
      ) : (
        summary.data && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base text-fg">
              {summary.data.total > 0 ? (
                <>
                  Сбоев за период: <b>{fmtNum(summary.data.total)}</b>
                  {needHands > 0 && (
                    <> · требуют рук: <b className="text-danger">{fmtNum(needHands)}</b></>
                  )}
                </>
              ) : (
                "Сбоев за период не зафиксировано"
              )}
            </span>
            <div className="ml-auto flex flex-wrap gap-1.5">
              <StageChip label="Все стадии" active={stage === ""} onClick={() => setStage("")} />
              {summary.data.by_stage.map((s) => (
                <StageChip
                  key={s.stage}
                  label={`${stageMeta(s.stage).label} · ${fmtNum(s.count)}`}
                  active={stage === s.stage}
                  onClick={() => setStage(stage === s.stage ? "" : s.stage)}
                />
              ))}
            </div>
          </div>
        )
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
        <Card>
          <CardHeader
            title="Отказы оплаты"
            subtitle="свежие сверху · строка открывает разбор"
            actions={
              stage ? (
                <Button size="sm" onClick={() => setStage("")}>
                  Все стадии
                </Button>
              ) : undefined
            }
          />
          {list.isError ? (
            <div className="p-4">
              <EmptyFailure
                what="журнал отказов оплаты"
                reason="Не смогли загрузить отказы. Это отказ запроса, а не отсутствие сбоев — не считайте, что всё прошло гладко."
                onRetry={() => list.refetch()}
              />
            </div>
          ) : (
            <LoadingGate
              loading={list.isLoading}
              skeleton={<SkeletonTable rows={6} cols={4} className="m-4 border-0" />}
              message="Поднимаю журнал отказов"
            >
              {/* `list.data &&` обязателен: первую секунду LoadingGate
                  рисует детей, и без проверки на ней мелькало бы «все
                  оплаты прошли» — ровно то враньё, из-за которого этот
                  экран переделывали. */}
              {list.data && rows.length === 0 ? (
                <AllPaymentsWentThrough filtered={Boolean(stage)} onReset={() => setStage("")} />
              ) : (
                <TableScroll className="max-h-[calc(100vh-320px)] rounded-none border-0">
                  <Table density="compact">
                    <THead>
                      <tr>
                        <TH>Когда</TH>
                        <TH>Стадия</TH>
                        <TH>Кто и сколько</TH>
                        <TH>Исход</TH>
                        <TH className="w-8" aria-label="Открыть разбор" />
                      </tr>
                    </THead>
                    <TBody>
                      {rows.map((row, i) => (
                        <ErrorRow
                          key={row.id}
                          row={row}
                          first={i === 0}
                          selected={selectedId === row.id}
                          onSelect={setSelectedId}
                        />
                      ))}
                    </TBody>
                  </Table>
                </TableScroll>
              )}
            </LoadingGate>
          )}
        </Card>

        <div className="lg:sticky lg:top-4 lg:self-start">
          {selected ? (
            <ErrorDetail row={selected} onClose={() => setSelectedId(null)} />
          ) : (
            <div className="rounded-lg border border-dashed border-border p-6 text-center text-base text-fg-muted">
              Выберите строку — здесь будет видно, на каком шаге сорвалось,
              ушли ли деньги и что делать дальше.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StageChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-sm border px-2 py-0.5 text-xs font-medium transition-colors",
        active
          ? "border-accent-7 bg-accent-3 text-accent-text"
          : "border-border text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}

/** Пусто — это хорошая новость, но только если запрос отработал. */
function AllPaymentsWentThrough({
  filtered,
  onReset,
}: {
  filtered: boolean;
  onReset: () => void;
}) {
  if (filtered) {
    return (
      <div className="px-4 py-10 text-center">
        <div className="text-base font-medium text-fg">На этой стадии сбоев нет</div>
        <div className="mt-1 text-base text-fg-muted">
          За выбранное окно ни один платёж не сорвался именно здесь.
        </div>
        <div className="mt-3">
          <Button onClick={onReset}>Показать все стадии</Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <CheckCircle2 className="h-6 w-6 text-success" aria-hidden />
      <div className="text-base font-medium text-fg">Все оплаты прошли</div>
      <div className="max-w-sm text-base text-fg-muted">
        За выбранное окно ни один платёж не сорвался. Журнал проверен — это
        не пустой экран из-за ошибки загрузки.
      </div>
    </div>
  );
}

function ErrorRow({
  row,
  first,
  selected,
  onSelect,
}: {
  row: PaymentErrorRow;
  first: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const meta = stageMeta(row.stage);
  const outcome = outcomeOf(row);

  return (
    <TR
      interactive
      first={first}
      tone={outcome.needsHands ? "failure" : "none"}
      onActivate={() => onSelect(row.id)}
      className={cn(selected && "bg-accent-3")}
      aria-current={selected ? "true" : undefined}
    >
      <TD className="whitespace-nowrap">
        <div className="text-fg">{fmtRelative(row.created_at)}</div>
        <div className="text-2xs text-fg-subtle">{fmtDate(row.created_at)}</div>
      </TD>
      <TD>
        <div className="truncate text-fg">{meta.label}</div>
        <div className="truncate text-2xs text-fg-subtle">
          шаг {meta.step} · {providerLabel(row.payment_provider)}
        </div>
      </TD>
      <TD>
        <div className="truncate text-fg">
          {row.username ? `@${row.username}` : row.telegram_id ? `tg:${row.telegram_id}` : "—"}
        </div>
        <div className="truncate text-2xs text-fg-subtle">
          {row.amount_rubles != null
            ? fmtRub(row.amount_rubles)
            : row.purchase_price_rubles != null
              ? fmtRub(row.purchase_price_rubles)
              : "сумма неизвестна"}
        </div>
      </TD>
      <TD>
        <StatusBadge kind={outcome.kind}>{outcome.title}</StatusBadge>
      </TD>
      <TD>
        <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
      </TD>
    </TR>
  );
}

/** Путь платежа из четырёх шагов и место, где он оборвался. */
function Pipeline({ step, known }: { step: StageStep; known: boolean }) {
  return (
    <ol className="mt-3 space-y-1.5">
      {([1, 2, 3, 4] as StageStep[]).map((n) => {
        const passed = n < step;
        const broken = n === step;
        return (
          <li key={n} className="flex items-center gap-2 text-base">
            <span
              className={cn(
                "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-2xs font-medium",
                broken
                  ? "bg-danger text-bg-card"
                  : passed
                    ? "bg-success/12 text-success"
                    : "bg-bg-subtle text-fg-subtle",
              )}
              aria-hidden
            >
              {n}
            </span>
            <span className={broken ? "font-medium text-fg" : "text-fg-muted"}>
              {STEP_TITLES[n]}
            </span>
            {broken && (
              <span className="text-xs font-medium text-danger">
                {known ? "здесь оборвалось" : "предположительно здесь"}
              </span>
            )}
            {passed && <span className="text-xs text-fg-subtle">пройден</span>}
          </li>
        );
      })}
    </ol>
  );
}

function ErrorDetail({ row, onClose }: { row: PaymentErrorRow; onClose: () => void }) {
  const meta = stageMeta(row.stage);
  const outcome = outcomeOf(row);
  const known = isKnownStage(row.stage);

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge kind="failure">{meta.label}</StatusBadge>
            <StatusBadge kind={outcome.kind}>{outcome.title}</StatusBadge>
          </div>
          <div className="mt-1 text-xs text-fg-muted">
            {fmtDate(row.created_at)} · {providerLabel(row.payment_provider)}
            {row.telegram_id ? ` · tg:${row.telegram_id}` : ""}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть разбор"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
        >
          ×
        </button>
      </div>

      <Pipeline step={meta.step} known={known} />

      <div className="mt-3 space-y-2 border-t border-border-subtle pt-3">
        <Block title="Что произошло" text={meta.what} />
        <Block title="Деньги и доступ" text={outcome.detail} />
        <Block title="Что делать" text={meta.next} emphasis />
      </div>

      <dl className="mt-3 space-y-1.5 border-t border-border-subtle pt-3 text-base">
        <Pair
          label="Сумма попытки"
          value={row.amount_rubles != null ? fmtRub(row.amount_rubles) : "не записана"}
        />
        <Pair
          label="Покупка"
          value={
            row.purchase_status
              ? `${row.purchase_tariff ?? row.purchase_type ?? "—"} · ${row.purchase_status}`
              : "не связана со счётом"
          }
        />
        <Pair
          label="Доступ сейчас"
          value={
            row.subscription_expires_at
              ? `до ${fmtDate(row.subscription_expires_at)}`
              : "подписки нет"
          }
        />
        {row.error_code && <Pair label="Код" value={row.error_code} mono />}
        {row.purchase_id && <Pair label="Номер покупки" value={row.purchase_id} mono />}
      </dl>

      {row.error_message && (
        <div className="mt-3">
          <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
            Текст ошибки
          </div>
          {/* Секреты уже вычищены на сервере (scrub_secrets): в тексте
              исключения жил URL метода Telegram вместе с токеном бота. */}
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-bg-subtle p-2 font-mono text-2xs text-fg-muted">
            {row.error_message}
          </pre>
        </div>
      )}

      {row.telegram_id && (
        <div className="mt-3 flex justify-end">
          <Link
            to={`/users?tg=${row.telegram_id}`}
            className="inline-flex items-center gap-1 text-xs text-accent-text underline-offset-2 hover:underline"
          >
            Открыть карточку и выдать доступ
            <ExternalLink className="h-3 w-3" aria-hidden />
          </Link>
        </div>
      )}
    </Card>
  );
}

function Block({
  title,
  text,
  emphasis,
}: {
  title: string;
  text: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
        {title}
      </div>
      <p className={emphasis ? "mt-0.5 text-base text-fg" : "mt-0.5 text-base text-fg-muted"}>
        {text}
      </p>
    </div>
  );
}

function Pair({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-fg-muted">{label}</dt>
      <dd
        className={mono ? "min-w-0 truncate font-mono text-xs text-fg" : "min-w-0 truncate text-fg"}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}
