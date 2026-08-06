import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Minus, Percent, Plus } from "lucide-react";

import { ApiError, endpoints, type UserDetail } from "@/lib/api";
import { fmtDate, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, Card, ConfirmDialog, Input, UndoBanner } from "@/components/ui";

/**
 * Деньги пользователя: баланс, личная скидка, скидка на ГБ обхода,
 * фиксированный процент кешбэка.
 *
 * ПРАВКА БАЛАНСА ПОДТВЕРЖДАЕТСЯ И ОТМЕНЯЕТСЯ. Диалог называет сумму,
 * человека и то, каким станет баланс, — не «вы уверены?». После списания
 * или начисления над блоком встаёт неисчезающий баннер с кнопкой
 * «Отменить»: она отправляет ОБРАТНУЮ операцию на ту же сумму с пометкой
 * «отмена». Это честная отмена, а не показная: в balance_transactions
 * останутся обе строки, и по журналу видно, что произошло.
 *
 * ПОЧЕМУ БАННЕР, А НЕ ТОСТ С КНОПКОЙ. Исчезающий тост с интерактивом
 * нарушает WCAG 2.2.1: сообщение пропадает раньше, чем до него доберутся
 * с клавиатуры (ux-patterns §2.4).
 *
 * ЗДЕСЬ ЖИЛ БАГ. Старая кнопка «Списать» делала setDelta(-x) и вызывала
 * мутацию через setTimeout: мутация читала сумму из замыкания, то есть
 * прежнюю, и списание уходило со старым знаком. Сумма теперь передаётся
 * аргументом мутации и в состоянии не живёт вовсе.
 */

function who(data: UserDetail, telegramId: number): string {
  const username = data.user.username;
  return typeof username === "string" && username ? `@${username}` : `tg:${telegramId}`;
}

interface BalanceOp {
  delta: number;
  reason?: string;
}

/** Часы жизни скидки. Пусто или мусор — бессрочно (сервер ждёт null). */
function hoursOrNull(raw: string): number | null {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

export function UserMoneyActions({
  telegramId,
  data,
  onChanged,
}: {
  telegramId: number;
  data: UserDetail;
  onChanged: () => void;
}) {
  const name = who(data, telegramId);
  const fail = (e: unknown) =>
    toast.error((e as ApiError)?.detail ?? "Не получилось. Ничего не изменилось.");

  // ── Баланс ────────────────────────────────────────────────────────
  const [amount, setAmount] = useState<string>("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<BalanceOp | null>(null);
  const [done, setDone] = useState<{ op: BalanceOp; newBalance: number } | null>(null);

  const parsed = Number(amount.replace(",", "."));
  const valid = Number.isFinite(parsed) && parsed > 0;

  const change = useMutation({
    mutationFn: (op: BalanceOp) =>
      endpoints.userBalanceChange(telegramId, {
        delta_rubles: op.delta,
        reason: op.reason || undefined,
      }),
    onSuccess: (res, op) => {
      setPending(null);
      setAmount("");
      setReason("");
      setDone({ op, newBalance: res.new_balance_rubles });
      onChanged();
    },
    onError: (e) => {
      setPending(null);
      fail(e);
    },
  });

  const undo = useMutation({
    mutationFn: (op: BalanceOp) =>
      endpoints.userBalanceChange(telegramId, {
        delta_rubles: -op.delta,
        reason: `Отмена правки: ${op.reason || "правка баланса из дашборда"}`,
      }),
    onSuccess: () => {
      setDone(null);
      toast.success(`${name}: правка баланса отменена обратной операцией`);
      onChanged();
    },
    onError: fail,
  });

  // ── Скидки и кешбэк ───────────────────────────────────────────────
  const [discountPercent, setDiscountPercent] = useState(30);
  const [discountHours, setDiscountHours] = useState<string>("24");
  const [trafficPercent, setTrafficPercent] = useState(30);
  const [trafficHours, setTrafficHours] = useState<string>("24");
  const [cashback, setCashback] = useState<string>(
    data.cashback_fixed_percent != null ? String(data.cashback_fixed_percent) : "10",
  );
  const [confirm, setConfirm] = useState<
    null | "discount-delete" | "traffic-delete" | "cashback-set" | "cashback-clear"
  >(null);

  const discountCreate = useMutation({
    mutationFn: () =>
      endpoints.userDiscountCreate(telegramId, {
        percent: discountPercent,
        expires_in_hours: hoursOrNull(discountHours),
      }),
    onSuccess: () => {
      toast.success(`${name}: скидка ${discountPercent}% на подписку выдана`);
      onChanged();
    },
    onError: fail,
  });

  const discountDelete = useMutation({
    mutationFn: () => endpoints.userDiscountDelete(telegramId),
    onSuccess: () => {
      setConfirm(null);
      toast.success(`${name}: скидка на подписку снята`);
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const trafficCreate = useMutation({
    mutationFn: () =>
      endpoints.userTrafficDiscountCreate(telegramId, {
        percent: trafficPercent,
        expires_in_hours: hoursOrNull(trafficHours),
      }),
    onSuccess: () => {
      toast.success(`${name}: скидка ${trafficPercent}% на пакеты ГБ выдана`);
      onChanged();
    },
    onError: fail,
  });

  const trafficDelete = useMutation({
    mutationFn: () => endpoints.userTrafficDiscountDelete(telegramId),
    onSuccess: () => {
      setConfirm(null);
      toast.success(`${name}: скидка на пакеты ГБ снята`);
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const cashbackSet = useMutation({
    mutationFn: () =>
      endpoints.userCashbackFixSet(telegramId, { percent: Number(cashback) || 0 }),
    onSuccess: (res) => {
      setConfirm(null);
      if (res.notify_sent) {
        toast.success(`${name}: ставка ${res.percent}% зафиксирована, человек уведомлён`);
      } else {
        toast.info(
          `${name}: ставка ${res.percent}% зафиксирована. Уведомление не дошло — возможно, бот заблокирован.`,
        );
      }
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const cashbackClear = useMutation({
    mutationFn: () => endpoints.userCashbackFixClear(telegramId),
    onSuccess: (res) => {
      setConfirm(null);
      toast.success(`${name}: фикс снят, применяется ${res.effective_percent}%`);
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const discount = data.discount as Record<string, unknown> | null;
  const trafficDiscount = data.traffic_discount as Record<string, unknown> | null;
  const fixed = data.cashback_fixed_percent;

  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
        Деньги
      </div>

      {done && (
        <UndoBanner
          className="mt-3"
          open
          seconds={30}
          message={
            <>
              {name}: {done.op.delta > 0 ? "начислено" : "списано"}{" "}
              <b>{fmtRub(Math.abs(done.op.delta))}</b>, баланс{" "}
              {fmtRub(done.newBalance)}
            </>
          }
          onUndo={() => undo.mutate(done.op)}
          onDismiss={() => setDone(null)}
        />
      )}

      {/* Баланс */}
      <div className="mt-3 flex items-baseline justify-between">
        <span className="text-base text-fg-muted">Баланс сейчас</span>
        <span className="text-lg font-semibold tabular-nums text-fg">
          {fmtRub(data.balance_rubles)}
        </span>
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Input
          label="Сумма, ₽"
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="500"
          hint="без знака — знак задаёт кнопка"
        />
        <Input
          label="Причина (попадёт в журнал)"
          value={reason}
          maxLength={200}
          onChange={(e) => setReason(e.target.value)}
          placeholder="компенсация за сбой"
        />
      </div>
      <div className="mt-2 flex gap-2">
        <Button
          variant="primary"
          icon={<Plus className="h-3.5 w-3.5" />}
          disabled={!valid}
          onClick={() => setPending({ delta: parsed, reason })}
        >
          Начислить
        </Button>
        <Button
          icon={<Minus className="h-3.5 w-3.5" />}
          disabled={!valid}
          onClick={() => setPending({ delta: -parsed, reason })}
        >
          Списать
        </Button>
      </div>

      {/* Личная скидка на подписку */}
      <div className="mt-4 border-t border-border-subtle pt-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-base font-medium text-fg">Скидка на подписку</span>
          {discount && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger hover:text-danger"
              onClick={() => setConfirm("discount-delete")}
            >
              Снять
            </Button>
          )}
        </div>
        {discount ? (
          <p className="mt-0.5 text-xs text-fg-muted">
            Сейчас {String(discount.discount_percent ?? "—")}%
            {discount.expires_at
              ? ` до ${fmtDate(String(discount.expires_at))}`
              : " бессрочно"}
          </p>
        ) : (
          <p className="mt-0.5 text-xs text-fg-muted">Персональной скидки нет.</p>
        )}
        <PercentRow
          percent={discountPercent}
          onPercent={setDiscountPercent}
          hours={discountHours}
          onHours={setDiscountHours}
          loading={discountCreate.isPending}
          onApply={() => discountCreate.mutate()}
        />
      </div>

      {/* Скидка на пакеты ГБ обхода */}
      <div className="mt-4 border-t border-border-subtle pt-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-base font-medium text-fg">Скидка на пакеты ГБ обхода</span>
          {trafficDiscount && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger hover:text-danger"
              onClick={() => setConfirm("traffic-delete")}
            >
              Снять
            </Button>
          )}
        </div>
        {trafficDiscount ? (
          <p className="mt-0.5 text-xs text-fg-muted">
            Сейчас {String(trafficDiscount.discount_percent ?? "—")}%
            {trafficDiscount.expires_at
              ? ` до ${fmtDate(String(trafficDiscount.expires_at))}`
              : " бессрочно"}
          </p>
        ) : (
          <p className="mt-0.5 text-xs text-fg-muted">
            Отдельная скидка, к подписке не относится.
          </p>
        )}
        <PercentRow
          percent={trafficPercent}
          onPercent={setTrafficPercent}
          hours={trafficHours}
          onHours={setTrafficHours}
          loading={trafficCreate.isPending}
          onApply={() => trafficCreate.mutate()}
        />
      </div>

      {/* Фиксированный кешбэк */}
      <div className="mt-4 border-t border-border-subtle pt-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-base font-medium text-fg">Кешбэк партнёра</span>
          {fixed != null && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger hover:text-danger"
              onClick={() => setConfirm("cashback-clear")}
            >
              Снять фикс
            </Button>
          )}
        </div>
        <p className="mt-0.5 text-xs text-fg-muted">
          Применяется {data.cashback_effective_percent}% —{" "}
          {fixed != null
            ? "зафиксировано админом, уровень рефералов не учитывается"
            : "по уровню рефералов"}
          .
        </p>
        <div className="mt-2 flex items-end gap-2">
          <div className="w-28">
            <Input
              label="Процент"
              inputMode="numeric"
              value={cashback}
              onChange={(e) => setCashback(e.target.value)}
              trailing={<Percent className="h-3.5 w-3.5" />}
            />
          </div>
          <Button
            onClick={() => setConfirm("cashback-set")}
            disabled={!Number.isFinite(Number(cashback))}
          >
            {fixed != null ? "Обновить фикс" : "Зафиксировать"}
          </Button>
        </div>
      </div>

      {/* ── Подтверждения ─────────────────────────────────────────── */}
      <ConfirmDialog
        open={pending !== null}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && change.mutate(pending)}
        loading={change.isPending}
        destructive={(pending?.delta ?? 0) < 0}
        title={
          (pending?.delta ?? 0) > 0
            ? `Начислить ${fmtRub(Math.abs(pending?.delta ?? 0))} — ${name}`
            : `Списать ${fmtRub(Math.abs(pending?.delta ?? 0))} — ${name}`
        }
        confirmLabel={(pending?.delta ?? 0) > 0 ? "Начислить" : "Списать"}
        body={
          <>
            Баланс станет{" "}
            <b>{fmtRub(data.balance_rubles + (pending?.delta ?? 0))}</b> вместо{" "}
            {fmtRub(data.balance_rubles)}.
            <div className="mt-2">
              Это реальные деньги пользователя: с баланса он покупает подписку и
              пакеты ГБ. Операция попадёт в журнал с вашим идентификатором.
            </div>
          </>
        }
      />

      <ConfirmDialog
        open={confirm === "discount-delete"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => discountDelete.mutate()}
        destructive
        loading={discountDelete.isPending}
        title={`Снять скидку на подписку — ${name}`}
        confirmLabel="Снять скидку"
        cancelLabel="Оставить скидку"
        body={`Следующая покупка пойдёт по полной цене. Скидка ${String(
          discount?.discount_percent ?? "",
        )}% исчезнет сразу, в том числе если человек уже открыл экран оплаты.`}
      />

      <ConfirmDialog
        open={confirm === "traffic-delete"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => trafficDelete.mutate()}
        destructive
        loading={trafficDelete.isPending}
        title={`Снять скидку на пакеты ГБ — ${name}`}
        confirmLabel="Снять скидку"
        cancelLabel="Оставить скидку"
        body="Пакеты ГБ обхода начнут продаваться по полной цене. На подписку это не влияет."
      />

      <ConfirmDialog
        open={confirm === "cashback-set"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => cashbackSet.mutate()}
        loading={cashbackSet.isPending}
        title={`Зафиксировать кешбэк ${Number(cashback) || 0}% — ${name}`}
        confirmLabel="Зафиксировать"
        body={
          <>
            С каждой покупки по его ссылке на баланс будет уходить{" "}
            <b>{Number(cashback) || 0}%</b> — это деньги из выручки, и ставка
            перестанет зависеть от уровня рефералов.
            <div className="mt-2">
              Человеку уйдёт поздравление с партнёрской ссылкой. Отменить
              уведомление после отправки нельзя.
            </div>
          </>
        }
      />

      <ConfirmDialog
        open={confirm === "cashback-clear"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => cashbackClear.mutate()}
        destructive
        loading={cashbackClear.isPending}
        title={`Снять фикс кешбэка — ${name}`}
        confirmLabel="Снять фикс"
        cancelLabel="Оставить фикс"
        body="Ставка вернётся к обычной логике: уровень рефералов плюс сохранённый минимум. Уведомление человеку не уходит."
      />
    </Card>
  );
}

/** Пара «процент + срок» и кнопка. Одинаковая у обеих скидок. */
function PercentRow({
  percent,
  onPercent,
  hours,
  onHours,
  loading,
  onApply,
}: {
  percent: number;
  onPercent: (v: number) => void;
  hours: string;
  onHours: (v: string) => void;
  loading: boolean;
  onApply: () => void;
}) {
  const valid = percent >= 1 && percent <= 100;
  return (
    <div className="mt-2 flex items-end gap-2">
      <div className="w-24">
        <Input
          label="Процент"
          inputMode="numeric"
          value={percent}
          onChange={(e) => onPercent(Number(e.target.value) || 0)}
          trailing={<Percent className="h-3.5 w-3.5" />}
        />
      </div>
      <div className="w-28">
        <Input
          label="Часов"
          inputMode="numeric"
          value={hours}
          onChange={(e) => onHours(e.target.value)}
          placeholder="бессрочно"
        />
      </div>
      <Button variant="primary" loading={loading} disabled={!valid} onClick={onApply}>
        Выдать
      </Button>
    </div>
  );
}
