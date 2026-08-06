import type { ReactNode } from "react";
import { ChevronDown, ChevronRight, Clock, Database, Receipt, Wrench } from "lucide-react";

import type { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import {
  Button,
  Card,
  StatusBadge,
  Table,
  TableScroll,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";

/**
 * Карточка пострадавшего от «premium на 10 лет».
 *
 * Что это за люди: старый flow покупки трафика перезаписывал реальную
 * подписку на bypass-only с сроком на десять лет вперёд. Бэкенд
 * (`database/admin.py:get_bypass_overwrite_victims`) ловит их по
 * is_bypass_only=TRUE AND expires_at > NOW+3y AND есть платная история и
 * предлагает корректный expires_at = MAX(subscription_history.end_date).
 *
 * ПОЧЕМУ ВСЯ ИСТОРИЯ ЛЕЖИТ ВНУТРИ СТРОКИ. Кнопка «Восстановить» меняет
 * дату окончания оплаченного доступа — то есть трогает деньги. Решение
 * нельзя принимать по одному числу, поэтому раскрытая строка показывает
 * ровно то, из чего это число посчитано: платежи, продления, пакеты ГБ.
 *
 * ДАТЫ — ЧЕРЕЗ ОБЩИЙ fmtDate. Раньше этот экран держал три собственные
 * функции форматирования, и «01.02.2026» на нём означало не то же самое,
 * что на соседнем.
 */

type Victim = Awaited<ReturnType<typeof endpoints.bypassAuditList>>["victims"][number];

const dateOnly = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
};

const dateTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

export const daysFromNow = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return Math.round((d.getTime() - Date.now()) / 86_400_000);
};

export function BypassVictim({
  v,
  expanded,
  onToggle,
  onFix,
  fixing,
}: {
  v: Victim;
  expanded: boolean;
  onToggle: () => void;
  onFix: () => void;
  fixing: boolean;
}) {
  const currDays = daysFromNow(v.current_expires_at);
  const propDays = daysFromNow(v.proposed_expires_at);
  const historyDays = daysFromNow(v.history_end_date);
  const grace = v.grace_will_apply;
  const paidTotal = v.payments.reduce((a, p) => a + (p.amount_rubles || 0), 0);

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        {/* Раскрытие и действие — две разные кнопки, а не кнопка внутри
            кнопки: вложенный <button> невалиден и на клавиатуре ведёт
            себя непредсказуемо. Именно так было в прежнем экране. */}
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-md py-1 text-left transition-colors hover:bg-bg-subtle"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-base font-medium text-fg">
                {v.username ? `@${v.username}` : `tg:${v.telegram_id}`}
              </span>
              <span className="font-mono text-2xs tabular-nums text-fg-subtle">
                tg:{v.telegram_id}
              </span>
              {v.current_is_combo && <StatusBadge kind="info">комбо</StatusBadge>}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-fg-muted">
              <span className="tabular-nums">
                {fmtNum(v.payments_count)} платежей · {fmtRub(paidTotal)}
              </span>
              <span className="tabular-nums">
                {fmtNum(v.traffic_purchases.length)} паков · {fmtNum(v.traffic_total_gb)} ГБ
              </span>
            </div>
          </div>
        </button>

        {/* Было → станет. Слова «сейчас» и «будет» стоят рядом с числами:
            без них две одинаковые плашки различались бы только цветом. */}
        <div className="flex shrink-0 items-center gap-2">
          <Capsule label="Сейчас" tone="risk" days={currDays} />
          <ChevronRight className="h-3 w-3 shrink-0 text-fg-subtle" aria-hidden />
          <Capsule
            label={grace ? "Будет (+1 день)" : "Будет"}
            tone={grace ? "info" : "success"}
            days={propDays}
          />
        </div>

        {v.can_fix ? (
          <Button
            variant="primary"
            icon={<Wrench className="h-3.5 w-3.5" />}
            onClick={onFix}
            loading={fixing}
          >
            Восстановить
          </Button>
        ) : (
          <StatusBadge kind="neutral">нет истории</StatusBadge>
        )}
      </div>

      {expanded && (
        <div className="border-t border-border-subtle bg-bg-subtle/40 p-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Block title="Текущее состояние">
              <KV label="expires_at" value={dateTime(v.current_expires_at)} />
              <KV
                label="через дней"
                value={currDays != null ? fmtNum(currDays) : "—"}
              />
              <KV label="is_bypass_only" value={v.current_is_bypass_only ? "TRUE" : "FALSE"} />
              <KV label="type" value={v.current_subscription_type ?? "—"} />
              <KV label="source" value={v.current_source ?? "—"} />
            </Block>
            <Block title="Будет применено">
              <KV label="expires_at" value={dateTime(v.proposed_expires_at)} />
              <KV
                label="через дней"
                value={propDays != null ? fmtNum(propDays) : "—"}
              />
              {grace && (
                <KV
                  label="grace"
                  value={
                    historyDays != null
                      ? `+1 день (история истекла ${fmtNum(Math.abs(historyDays))} дн назад)`
                      : "+1 день"
                  }
                />
              )}
              <KV label="is_bypass_only" value="FALSE" />
              <KV label="source" value="payment (если был bypass_only)" />
              <KV label="источник даты" value={v.last_paid_action_type ?? "—"} />
            </Block>
          </div>

          <Sub title={`Платежи · ${v.payments.length}`} icon={<Receipt className="h-3.5 w-3.5" />}>
            {v.payments.length === 0 ? (
              <div className="text-xs text-fg-subtle">Нет одобренных платежей.</div>
            ) : (
              <TableScroll>
                <Table density="compact">
                  <THead>
                    <TR>
                      <TH>Когда</TH>
                      <TH>Тариф</TH>
                      <TH numeric>Сумма</TH>
                      <TH>Purchase ID</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {v.payments.map((p) => (
                      <TR key={p.id}>
                        <TD>{dateTime(p.paid_at ?? p.created_at)}</TD>
                        <TD mono>{p.tariff}</TD>
                        <TD numeric>{fmtRub(p.amount_rubles)}</TD>
                        <TD mono>{p.purchase_id ? p.purchase_id.slice(0, 16) : "—"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableScroll>
            )}
          </Sub>

          <Sub
            title={`История подписок · ${v.history.length}`}
            icon={<Clock className="h-3.5 w-3.5" />}
          >
            {v.history.length === 0 ? (
              <div className="text-xs text-fg-subtle">История пуста.</div>
            ) : (
              <TableScroll>
                <Table density="compact">
                  <THead>
                    <TR>
                      <TH>Когда</TH>
                      <TH>Действие</TH>
                      <TH>Начало</TH>
                      <TH>Конец</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {v.history.map((h) => {
                      // Платные действия помечены словом и значком, а не
                      // одним цветом: именно из них берётся предлагаемая
                      // дата, и разница обязана читаться без цвета.
                      const paid = ["purchase", "renewal", "auto_renew"].includes(
                        h.action_type,
                      );
                      return (
                        <TR key={h.id}>
                          <TD>{dateTime(h.created_at)}</TD>
                          <TD>
                            <StatusBadge kind={paid ? "success" : "neutral"}>
                              {h.action_type}
                            </StatusBadge>
                          </TD>
                          <TD>{dateOnly(h.start_date)}</TD>
                          <TD>{dateOnly(h.end_date)}</TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              </TableScroll>
            )}
          </Sub>

          <Sub
            title={`Пакеты ГБ · ${v.traffic_purchases.length}`}
            icon={<Database className="h-3.5 w-3.5" />}
          >
            {v.traffic_purchases.length === 0 ? (
              <div className="text-xs text-fg-subtle">Пакеты не покупал.</div>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {v.traffic_purchases.map((t) => (
                  <div key={t.id} className="rounded-md border border-border p-3">
                    <div className="text-base font-medium tabular-nums text-fg">
                      {fmtNum(t.gb_amount)} ГБ
                    </div>
                    <div className="mt-0.5 text-2xs tabular-nums text-fg-muted">
                      {fmtRub(t.price_rub)} · {dateOnly(t.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Sub>
        </div>
      )}
    </Card>
  );
}

function Capsule({
  label,
  tone,
  days,
}: {
  label: string;
  tone: "risk" | "success" | "info";
  days: number | null;
}) {
  const cls =
    tone === "risk"
      ? "border-risk/30 bg-risk/8 text-risk"
      : tone === "info"
        ? "border-info/30 bg-info/8 text-info"
        : "border-success/30 bg-success/8 text-success";
  return (
    <div className={`rounded-md border px-2.5 py-1 text-right ${cls}`}>
      <div className="text-2xs font-medium">{label}</div>
      <div className="text-xs font-semibold tabular-nums">
        {days != null ? `+${fmtNum(days)} дн` : "—"}
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-bg-card p-3">
      <div className="text-xs font-medium text-fg-subtle">{title}</div>
      <dl className="mt-2 space-y-1.5">{children}</dl>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <dt className="shrink-0 text-fg-muted">{label}</dt>
      <dd className="truncate font-mono text-fg">{value}</dd>
    </div>
  );
}

function Sub({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mt-4 rounded-md border border-border bg-bg-card p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-fg-subtle">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}
