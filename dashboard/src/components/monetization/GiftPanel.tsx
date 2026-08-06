import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import { ApiError, endpoints } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Dash,
  EmptyFailure,
  LoadingGate,
  SkeletonCard,
  StatusBadge,
} from "@/components/ui";
import { CopyField } from "./CopyField";
import { UsageMeter } from "./UsageMeter";
import { giftState, usageOf } from "./labels";

/**
 * Разбор одной подарочной ссылки: что выдаёт, сколько потрачено, кому.
 *
 * ССЫЛКА ПРИХОДИТ С СЕРВЕРА. Раньше диплинк склеивался прямо здесь из
 * строки с именем бота, зашитой в разметку, — и имя там было не то,
 * которое стоит в config.BOT_USERNAME. Скопированная ссылка вела в
 * никуда, и понять это можно было только попробовав перейти.
 *
 * УДАЛЕНИЕ МЯГКОЕ, И ДИАЛОГ ГОВОРИТ ИМЕННО ЭТО. «Удалить ссылку?» без
 * продолжения заставляет гадать, отберут ли гигабайты у тех, кто уже
 * активировал. Не отберут — и это написано.
 */
export function GiftPanel({
  linkId,
  onClose,
  onDeleted,
}: {
  linkId: number;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);

  const detail = useQuery({
    queryKey: ["bgift", "detail", linkId],
    queryFn: () => endpoints.bgiftDetail(linkId),
  });
  const redemptions = useQuery({
    queryKey: ["bgift", "redemptions", linkId],
    queryFn: () => endpoints.bgiftRedemptions(linkId, 200),
  });

  const del = useMutation({
    mutationFn: () => endpoints.bgiftDelete(linkId),
    onSuccess: () => {
      toast.success("Ссылка удалена — выданные гигабайты остались у людей");
      setConfirm(false);
      qc.invalidateQueries({ queryKey: ["bgift"] });
      onDeleted();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось удалить ссылку"),
  });

  if (detail.isError) {
    return (
      <EmptyFailure
        what="карточку подарочной ссылки"
        reason="Данные по этой ссылке не пришли. Остальной список работает."
        onRetry={() => detail.refetch()}
      />
    );
  }

  const b = detail.data;
  const state = b ? giftState(b) : null;
  const use = usageOf(b?.redemption_count, b?.max_uses);
  const left = use.max === null ? null : Math.max(0, use.max - use.used);

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={<span className="font-mono">{b?.code ?? "…"}</span>}
          subtitle={b ? `создана ${fmtDate(b.created_at)}` : undefined}
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
        <CardBody className="space-y-3">
          <LoadingGate
            loading={detail.isLoading}
            skeleton={<SkeletonCard lines={3} />}
            message="Читаю подарочную ссылку"
          >
            {b && (
              <>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-2xl font-semibold tabular-nums text-fg">
                    {fmtNum(b.gb_amount)} ГБ
                  </span>
                  <span className="text-base text-fg-muted">каждому, кто перейдёт</span>
                  {state && <StatusBadge kind={state.kind}>{state.label}</StatusBadge>}
                </div>

                <CopyField value={b.t_me_url} label="ссылку" />

                <UsageMeter
                  used={b.redemption_count}
                  max={b.max_uses}
                  noun="активаций"
                  expiresAt={b.expires_at}
                />

                <p className="text-xs text-fg-muted">
                  {b.redemption_count == null
                    ? "Счётчик активаций не сошёлся — число выдач по этой ссылке сейчас неизвестно."
                    : left === null
                      ? `Выдано ${fmtNum(use.used * b.gb_amount)} ГБ, предела активаций нет.`
                      : left === 0
                        ? `Ссылка выбрана полностью: выдано ${fmtNum(use.used * b.gb_amount)} ГБ.`
                        : `Выдано ${fmtNum(use.used * b.gb_amount)} ГБ, осталось ещё на ${fmtNum(left)} ${
                            left === 1 ? "активацию" : "активаций"
                          } — до ${fmtNum(left * b.gb_amount)} ГБ.`}{" "}
                  Срок жизни ссылки — {fmtNum(b.validity_days)} дн. с момента создания.
                </p>

                {b.deleted_at ? (
                  <div className="rounded-md border border-border bg-bg-subtle p-3 text-base text-fg-muted">
                    Ссылка удалена {fmtDate(b.deleted_at)} и больше не работает.
                    Выданные по ней гигабайты остались у людей.
                  </div>
                ) : (
                  <div className="flex justify-end">
                    <Button variant="ghost" onClick={() => setConfirm(true)}>
                      Удалить ссылку
                    </Button>
                  </div>
                )}
              </>
            )}
          </LoadingGate>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Кто активировал"
          subtitle={
            redemptions.data ? `${fmtNum(redemptions.data.total)} всего` : "новые сверху"
          }
        />
        {redemptions.isError ? (
          <div className="p-4">
            <EmptyFailure
              what="список активаций"
              reason="Список не пришёл. Пустой список здесь читался бы как «никто не активировал»."
              onRetry={() => redemptions.refetch()}
            />
          </div>
        ) : (
          <LoadingGate
            loading={redemptions.isLoading}
            skeleton={<SkeletonCard lines={4} className="m-4 border-0" />}
            message="Читаю активации"
          >
            {redemptions.data && redemptions.data.rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-base text-fg-muted">
                По этой ссылке ещё никто не переходил.
              </div>
            ) : (
              <ul className="max-h-[360px] divide-y divide-border-subtle overflow-y-auto">
                {(redemptions.data?.rows ?? []).map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-base text-fg">
                        tg:{r.telegram_id ?? "—"}
                      </div>
                      <div className="text-2xs text-fg-subtle">
                        {r.redeemed_at ? fmtDate(r.redeemed_at) : <Dash />}
                      </div>
                    </div>
                    <span className="shrink-0 text-base tabular-nums text-fg">
                      +{fmtNum(r.gb_granted)} ГБ
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </LoadingGate>
        )}
      </Card>

      <ConfirmDialog
        open={confirm}
        onCancel={() => setConfirm(false)}
        onConfirm={() => del.mutate()}
        loading={del.isPending}
        destructive
        title={`Удалить ссылку ${b?.code ?? ""}`}
        confirmLabel="Удалить ссылку"
        cancelLabel="Оставить работать"
        requireText={b?.code}
        requireHint={`Наберите код ссылки — «${b?.code ?? ""}»`}
        body={
          <>
            Ссылка перестанет работать у всех, кому она разослана: перешедшие
            дальше получат обычное приветствие бота, а не гигабайты.
            <div className="mt-2">
              Уже выданные {fmtNum(use.used)}{" "}
              {use.used === 1 ? "активация" : "активаций"} остаются в силе —
              гигабайты у людей никто не отбирает, статистика сохраняется.
            </div>
          </>
        }
      />
    </div>
  );
}
