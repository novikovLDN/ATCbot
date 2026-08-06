import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import {
  Card,
  CardBody,
  CardHeader,
  Dash,
  EmptyFailure,
  LoadingGate,
  SkeletonCard,
} from "@/components/ui";

/**
 * Карточка партнёра: итоги, кого пригласил, что ему начислили.
 *
 * ПОЧЕМУ ЭТА ПАНЕЛЬ ПЕРЕПИСАНА ЦЕЛИКОМ. Прежняя читала из ответа ключи,
 * которых сервер не отдаёт: invited_count, paid_count,
 * total_invited_revenue, total_cashback_paid, current_cashback_percent —
 * ни одного из них в /referrals/{id} не было, поэтому во всех пяти
 * плитках стоял прочерк. Список приглашённых искался под именем
 * invited_users, а приходит invited_list — и у партнёра с сотней
 * приглашённых панель писала «никого нет». Теперь итоги подмешивает
 * сервер, а список читается под своим настоящим именем.
 *
 * ИТОГИ МОГУТ НЕ ПРИЙТИ. Сервер берёт их вторым запросом и при его
 * отказе присылает карточку без них. Прочерк в плитке подписан словами:
 * «не посчитали» — это не то же самое, что ноль.
 */
export function ReferrerPanel({
  referrerId,
  onClose,
}: {
  referrerId: number;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["referrals", "detail", referrerId],
    queryFn: () => endpoints.referrerDetail(referrerId),
  });
  const history = useQuery({
    queryKey: ["referrals", "history", referrerId],
    queryFn: () => endpoints.referrerHistory(referrerId, 100),
  });

  if (detail.isError) {
    return (
      <EmptyFailure
        what="карточку партнёра"
        reason="Данные по этому партнёру не пришли. Остальной список работает."
        onRetry={() => detail.refetch()}
      />
    );
  }

  const d = detail.data;
  const invited = d?.invited_list ?? [];
  const title = d?.username ? `@${d.username}` : `tg:${referrerId}`;

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={title}
          subtitle={`tg:${referrerId}`}
          actions={
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть карточку"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          }
        />
        <CardBody>
          <LoadingGate
            loading={detail.isLoading}
            skeleton={<SkeletonCard lines={3} />}
            message="Собираю итоги партнёра"
          >
            <dl className="grid grid-cols-2 gap-2">
              <Tile label="Пригласил" value={fmtNum(d?.invited_count)} />
              <Tile label="Из них купили" value={fmtNum(d?.paid_count)} />
              <Tile label="Принёс выручки" value={fmtRub(d?.total_invited_revenue)} />
              <Tile label="Получил кешбэком" value={fmtRub(d?.total_cashback_paid)} />
            </dl>
            <p className="mt-2 text-xs text-fg-muted">
              {d?.current_cashback_percent == null ? (
                "Текущий процент кешбэка посчитать не удалось — итоги пришли не полностью."
              ) : (
                <>
                  Сейчас получает{" "}
                  <b className="text-fg">{fmtNum(d.current_cashback_percent)}%</b> с
                  каждой покупки приглашённого.
                </>
              )}
            </p>
          </LoadingGate>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Кого пригласил"
          subtitle={`${fmtNum(invited.length)} · новые сверху`}
        />
        <LoadingGate
          loading={detail.isLoading}
          skeleton={<SkeletonCard lines={4} className="m-4 border-0" />}
          message="Читаю список приглашённых"
        >
          {detail.data && invited.length === 0 ? (
            <div className="px-4 py-6 text-center text-base text-fg-muted">
              По ссылке этого партнёра пока никто не пришёл.
            </div>
          ) : (
            <ul className="max-h-[300px] divide-y divide-border-subtle overflow-y-auto">
              {invited.slice(0, 50).map((u, i) => (
                <li
                  key={`${u.invited_user_id ?? i}`}
                  className="flex items-start justify-between gap-3 px-4 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-base text-fg">
                      {u.username ? `@${u.username}` : `tg:${u.invited_user_id ?? "—"}`}
                    </div>
                    <div className="text-2xs text-fg-subtle">
                      пришёл {u.registered_at ? fmtDate(u.registered_at) : "—"}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    {u.purchase_amount ? (
                      <>
                        <div className="text-base tabular-nums text-fg">
                          {fmtRub(u.purchase_amount)}
                        </div>
                        <div className="text-2xs text-fg-subtle">
                          кешбэк {fmtRub(u.cashback_amount)}
                        </div>
                      </>
                    ) : (
                      <span className="text-xs text-fg-muted">не покупал</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {invited.length > 50 && (
            <div className="border-t border-border-subtle px-4 py-2 text-xs text-fg-muted">
              Показаны первые 50 из {fmtNum(invited.length)}.
            </div>
          )}
        </LoadingGate>
      </Card>

      <Card>
        <CardHeader
          title="Начисления кешбэка"
          subtitle={history.data ? `${fmtNum(history.data.total)} всего` : "история выплат"}
        />
        {history.isError ? (
          <div className="p-4">
            <EmptyFailure
              what="историю начислений"
              reason="История не пришла. Пустой список здесь читался бы как «начислений не было»."
              onRetry={() => history.refetch()}
            />
          </div>
        ) : (
          <LoadingGate
            loading={history.isLoading}
            skeleton={<SkeletonCard lines={4} className="m-4 border-0" />}
            message="Читаю начисления"
          >
            {history.data && history.data.rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-base text-fg-muted">
                Начислений ещё не было.
              </div>
            ) : (
              <ul className="max-h-[320px] divide-y divide-border-subtle overflow-y-auto">
                {(history.data?.rows ?? []).map((r, i) => (
                  <li
                    key={i}
                    className="flex items-center justify-between gap-3 px-4 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-base text-fg">
                        {r.referred_username
                          ? `@${r.referred_username}`
                          : `tg:${r.referred_user_id ?? "—"}`}
                      </div>
                      <div className="text-2xs text-fg-subtle">
                        {r.created_at ? fmtDate(r.created_at) : <Dash />}
                      </div>
                    </div>
                    <span className="shrink-0 text-base tabular-nums text-fg">
                      {fmtRub(r.reward_amount)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </LoadingGate>
        )}
      </Card>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border px-2.5 py-2">
      <dt className="text-2xs text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 truncate text-lg font-semibold tabular-nums text-fg">{value}</dd>
    </div>
  );
}
