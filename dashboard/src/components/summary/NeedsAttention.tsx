import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Megaphone, ShieldAlert } from "lucide-react";

import { endpoints, type AttentionItem } from "@/lib/api";
import { fmtRelative, fmtRub } from "@/lib/format";
import { Card, CardHeader, EmptyFailure, LoadingGate, Skeleton } from "@/components/ui";

/**
 * ЗОНА C — «требует внимания». От нуля до десяти конкретных объектов.
 *
 * Не сводка и не счётчик: каждая строка — вещь, с которой нужно что-то
 * сделать, и ссылка ведёт прямо к ней. Принцип «чинить там, где нашёл»
 * (research §4.9).
 *
 * ПУСТО — ЭТО ХОРОШАЯ НОВОСТЬ, И ТАК И НАПИСАНО. Здесь нет ни «нет
 * данных», ни извинений: пустой список означает, что всё проверено и
 * всё в порядке.
 *
 * НО «ПРОБЛЕМ НЕТ» И «НЕ СМОГЛИ ПРОВЕРИТЬ» — РАЗНЫЕ СОСТОЯНИЯ, И
 * ВЫГЛЯДЯТ ОНИ ПО-РАЗНОМУ. Сервер присылает рядом со списком sources — по
 * строке на источник. Панель Remnawave отвалилась → над списком встаёт
 * предупреждение, называющее, что именно осталось непроверенным. Без
 * этого зелёная галочка врала бы ровно в тот момент, когда она дороже
 * всего.
 */

const SOURCE_LABELS: Record<string, string> = {
  payments: "зависшие платежи",
  panel: "сверку с VPN-панелью",
  broadcasts: "рассылки",
};

export function NeedsAttention() {
  const attention = useQuery({
    queryKey: ["summary", "attention"],
    queryFn: () => endpoints.summaryAttention(10),
    refetchInterval: 60_000,
  });

  if (attention.isError) {
    return (
      <EmptyFailure
        what="список проблем"
        reason="Проверка не отработала целиком. Это не значит, что проблем нет — значит, что мы их не увидели."
        onRetry={() => attention.refetch()}
      />
    );
  }

  const data = attention.data;
  const broken = data
    ? Object.entries(data.sources)
        .filter(([, state]) => state === "error")
        .map(([name]) => SOURCE_LABELS[name] ?? name)
    : [];

  return (
    <Card>
      <CardHeader
        title="Требует внимания"
        subtitle={
          data && data.truncated
            ? "показаны первые десять — остальное в разделах"
            : "зависшие платежи, расхождения с панелью, упавшие рассылки"
        }
      />
      <div className="p-4">
        <LoadingGate
          loading={attention.isLoading}
          skeleton={
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          }
          message="Проверяю платежи, панель и рассылки"
        >
          {broken.length > 0 && (
            <div className="mb-3 flex items-start gap-2 rounded-md border border-risk/40 bg-risk/[0.06] px-3 py-2.5">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-risk" aria-hidden />
              <div className="text-base text-fg">
                Не смогли проверить {broken.join(" и ")}. Список ниже неполный.
                <button
                  type="button"
                  onClick={() => attention.refetch()}
                  className="ml-2 text-accent-text underline-offset-2 hover:underline"
                >
                  Повторить
                </button>
              </div>
            </div>
          )}

          {data && data.items.length === 0 ? (
            <AllClear everythingChecked={broken.length === 0} />
          ) : (
            <ul className="divide-y divide-border-subtle">
              {(data?.items ?? []).map((item) => (
                <li key={item.id}>
                  <AttentionRow item={item} />
                </li>
              ))}
            </ul>
          )}
        </LoadingGate>
      </div>
    </Card>
  );
}

/** Пусто и всё проверено — это хорошая новость. Формулируем как новость. */
function AllClear({ everythingChecked }: { everythingChecked: boolean }) {
  if (!everythingChecked) {
    // Часть проверок не отработала — «всё чисто» здесь было бы неправдой.
    // Предупреждение уже стоит выше, тут просто не заявляем обратного.
    return (
      <div className="py-6 text-center text-base text-fg-muted">
        Среди проверенного проблем не нашлось.
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center">
      <CheckCircle2 className="h-6 w-6 text-success" aria-hidden />
      <div className="text-base font-medium text-fg">Разбирать нечего</div>
      <div className="max-w-sm text-base text-fg-muted">
        Платежи проходят, подписки сходятся с панелью, рассылки доехали.
      </div>
    </div>
  );
}

const ROW_ICON = {
  stuck_payment: Clock,
  panel_mismatch: ShieldAlert,
  failed_broadcast: Megaphone,
} as const;

const ROW_TONE = {
  stuck_payment: "text-warning",
  panel_mismatch: "text-risk",
  failed_broadcast: "text-danger",
} as const;

/**
 * Строка списка. Значок и слово рядом с цветом обязательны: цвет один
 * ничего не сообщает человеку с красно-зелёной дальтонизацией и не
 * переживает печать (research §4.11).
 */
function AttentionRow({ item }: { item: AttentionItem }) {
  const Icon = ROW_ICON[item.kind];
  const { to, title, detail } = describe(item);

  return (
    <Link
      to={to}
      className="flex items-start gap-3 rounded-md px-1 py-3 transition-colors hover:bg-bg-subtle"
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${ROW_TONE[item.kind]}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate text-base font-medium text-fg">{title}</div>
        <div className="truncate text-xs text-fg-muted">{detail}</div>
      </div>
      <div className="shrink-0 text-xs text-fg-subtle">
        {item.at ? fmtRelative(item.at) : "—"}
      </div>
    </Link>
  );
}

/** Куда ведёт строка и что на ней написано. Ссылка всегда ведёт к самому
 *  объекту, а не в раздел вообще: иначе искать его придётся заново. */
function describe(item: AttentionItem): { to: string; title: string; detail: string } {
  const who = item.username ? `@${item.username}` : `tg:${item.telegram_id}`;

  if (item.kind === "stuck_payment") {
    return {
      to: `/users?tg=${item.telegram_id}`,
      title: `Платёж завис в ожидании — ${who}`,
      detail: [
        item.amount_rubles != null ? fmtRub(item.amount_rubles) : null,
        item.tariff,
        item.provider,
      ]
        .filter(Boolean)
        .join(" · "),
    };
  }

  if (item.kind === "panel_mismatch") {
    return {
      to: `/service?focus=reconciliation&tg=${item.telegram_id}`,
      title: `Подписка не сошлась с панелью — ${who}`,
      detail:
        item.years_from_now != null
          ? `в Remnawave срок на ${item.years_from_now} лет вперёд`
          : "срок в панели больше выданного",
    };
  }

  return {
    to: item.broadcast_id ? `/broadcasts?id=${item.broadcast_id}` : "/broadcasts",
    title: `Рассылка не доехала — ${item.title ?? "без названия"}`,
    detail:
      item.error ??
      (item.failed != null && item.total != null
        ? `${item.failed} из ${item.total} не доставлено`
        : "планировщик записал ошибку"),
  };
}
