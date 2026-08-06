import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { endpoints, type PurchaseRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import {
  Card,
  CardHeader,
  Dash,
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
import {
  isExternalMoney,
  providerLabel,
  purchaseLabel,
  statusMeta,
} from "@/components/payments/labels";

/**
 * Покупки одного человека: подписки, пакеты ГБ, пополнения, всё остальное.
 *
 * ДВЕ СУММЫ, А НЕ ОДНА. Сверху отдельно «внешними оплатами» и отдельно
 * «с баланса». Складывать их нельзя: покупка с баланса — это трата уже
 * пополненных денег, и в выручке она не считается (см. правило в
 * app/api/dashboard/routes/payments.py). Старый экран показывал одно
 * число «потрачено» и завышал его ровно на эту величину.
 *
 * ОТКАЗ ЗАПРОСА НЕ РИСУЕТ «ПОКУПОК НЕТ». Раньше именно так и было: при
 * ошибке карточка показывала пустое состояние, то есть враньё про
 * платящего человека (аудит §2).
 *
 * Строка кликается и ведёт в разбор платежа на экране «Платежи» — там
 * видно провайдера, счёт и ошибки по этой самой покупке.
 */
export function UserPurchases({ telegramId }: { telegramId: number }) {
  const navigate = useNavigate();
  const purchases = useQuery({
    queryKey: ["users", "purchases", telegramId],
    queryFn: () => endpoints.userPayments(telegramId, 100),
    staleTime: 30_000,
  });

  const rows: PurchaseRow[] = purchases.data ?? [];
  const paid = rows.filter((r) => r.status === "paid");
  const external = paid.filter((r) => isExternalMoney(r.payment_provider));
  const fromBalance = paid.filter((r) => !isExternalMoney(r.payment_provider));
  const sum = (list: PurchaseRow[]) =>
    list.reduce((acc, r) => acc + (r.price_rubles ?? 0), 0);
  const waiting = rows.filter((r) => r.status === "pending").length;

  return (
    <Card>
      <CardHeader
        title="Покупки"
        subtitle={
          purchases.data
            ? [
                `внешними оплатами ${fmtRub(sum(external))} за ${fmtNum(external.length)}`,
                fromBalance.length
                  ? `с баланса ${fmtRub(sum(fromBalance))} за ${fmtNum(fromBalance.length)}`
                  : null,
                waiting ? `ждут оплаты ${fmtNum(waiting)}` : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : "подписки, пакеты ГБ, пополнения"
        }
      />

      {purchases.isError ? (
        <div className="p-4">
          <EmptyFailure
            what="покупки этого человека"
            reason="История не пришла. Это отказ запроса, а не отсутствие покупок — не начисляйте компенсацию по этому экрану."
            onRetry={() => purchases.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={purchases.isLoading}
          skeleton={<SkeletonTable rows={4} cols={4} className="m-4 border-0" />}
          message="Поднимаю историю покупок"
        >
          {/* `purchases.data &&` обязателен: первую секунду LoadingGate
              рисует детей, и без проверки мелькало бы «покупок нет». */}
          {purchases.data && rows.length === 0 ? (
            <div className="px-4 py-8 text-center text-base text-fg-muted">
              Покупок нет: ни подписки, ни пакетов ГБ, ни пополнений баланса.
            </div>
          ) : (
            <TableScroll className="max-h-[420px] rounded-none border-0">
              <Table density="compact">
                <THead>
                  <tr>
                    <TH>Когда</TH>
                    <TH>Что</TH>
                    <TH numeric>Сумма</TH>
                    <TH>Оплата</TH>
                    <TH>Статус</TH>
                  </tr>
                </THead>
                <TBody>
                  {rows.map((row, i) => {
                    const state = statusMeta(row.status);
                    const internal = !isExternalMoney(row.payment_provider);
                    return (
                      <TR
                        key={row.id ?? `${row.purchase_id}-${i}`}
                        interactive
                        first={i === 0}
                        onActivate={() => navigate(`/payments?purchase=${row.id}`)}
                      >
                        <TD className="whitespace-nowrap text-fg-muted">
                          {fmtDate(row.created_at)}
                        </TD>
                        <TD>
                          <div className="truncate text-fg">{purchaseLabel(row)}</div>
                          {row.promo_code && (
                            <div className="truncate font-mono text-2xs text-fg-subtle">
                              промокод {row.promo_code}
                            </div>
                          )}
                        </TD>
                        <TD numeric>
                          {row.price_rubles == null ? <Dash /> : fmtRub(row.price_rubles)}
                        </TD>
                        <TD>
                          <div className="whitespace-nowrap text-fg-muted">
                            {providerLabel(row.payment_provider)}
                          </div>
                          {internal && (
                            <div className="whitespace-nowrap text-2xs text-fg-subtle">
                              не выручка
                            </div>
                          )}
                        </TD>
                        <TD>
                          <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableScroll>
          )}
        </LoadingGate>
      )}
    </Card>
  );
}
