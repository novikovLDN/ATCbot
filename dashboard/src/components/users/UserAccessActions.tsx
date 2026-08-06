import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Crown, KeyRound, ShieldOff, Trash2 } from "lucide-react";

import { ApiError, endpoints, type UserDetail } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, Card, ConfirmDialog, Input } from "@/components/ui";

/**
 * Доступ: выдать, сменить тариф, отозвать, VIP, удалить.
 *
 * ПОДТВЕРЖДЕНИЕ ЕСТЬ У ВСЕГО, ЧТО НЕЛЬЗЯ ОТКАТИТЬ КНОПКОЙ. Отзыв доступа,
 * снятие VIP, смена тарифа и удаление проходят через ConfirmDialog, и в
 * тексте диалога стоит, ЧТО и С КЕМ произойдёт: «Отозвать доступ у
 * @ivanov — подписка до 14.09.2026 перестанет действовать». Вопроса «вы
 * уверены?» здесь нет ни в одном диалоге — он ничего не сообщает
 * (ux-patterns §2.1).
 *
 * Удаление требует набрать telegram_id — не слово «УДАЛИТЬ», а точный
 * идентификатор именно этого человека: так нельзя стереть не того
 * (приём GitHub Danger Zone, §2.3). Это единственная защита, которая
 * работала в старом экране, и она сохранена дословно.
 *
 * ВЫДАЧА ДОСТУПА ПОДТВЕРЖДЕНИЯ НЕ ТРЕБУЕТ: она обратима отзывом, а
 * диалог на каждое частое действие приводит к тому, что диалоги
 * перестают читать (§2.1, «cry wolf»).
 */

const TARIFFS: Array<{ value: string; label: string }> = [
  { value: "basic", label: "Базовый" },
  { value: "plus", label: "Плюс" },
  // Комбо — отдельные продукты со своей ценой и пакетом ГБ обхода, а не
  // галочка к обычной подписке. Значения обязаны совпадать с
  // config.GRANTABLE_TARIFF_TYPES: невалидный тариф вернёт 422.
  { value: "combo_basic", label: "Комбо Базовый" },
  { value: "combo_plus", label: "Комбо Плюс" },
];

const QUICK_DAYS = [7, 30, 90, 365];

function who(data: UserDetail, telegramId: number): string {
  const username = data.user.username;
  return typeof username === "string" && username ? `@${username}` : `tg:${telegramId}`;
}

export function UserAccessActions({
  telegramId,
  data,
  onChanged,
}: {
  telegramId: number;
  data: UserDetail;
  onChanged: () => void;
}) {
  const [days, setDays] = useState(30);
  const [tariff, setTariff] = useState("basic");
  const [confirm, setConfirm] = useState<
    null | "revoke" | "vip-grant" | "vip-revoke" | "switch" | "delete"
  >(null);

  const name = who(data, telegramId);
  const sub = data.subscription;
  const expiresAt = sub ? String(sub.expires_at ?? "") : "";
  const currentTariff = sub ? String(sub.tariff_display ?? sub.subscription_type ?? "—") : null;

  const fail = (e: unknown) =>
    toast.error((e as ApiError)?.detail ?? "Не получилось. Действие не выполнено.");

  const grant = useMutation({
    mutationFn: () => endpoints.userGrant(telegramId, { days, tariff }),
    onSuccess: (res) => {
      toast.success(
        `${name}: доступ до ${fmtDate(res.expires_at)}${res.vpn_key ? ", ключ выдан" : ""}`,
      );
      onChanged();
    },
    onError: fail,
  });

  const switchTariff = useMutation({
    mutationFn: () => endpoints.userSwitchTariff(telegramId, { tariff }),
    onSuccess: () => {
      toast.success(`${name}: тариф сменён на ${TARIFFS.find((t) => t.value === tariff)?.label}`);
      setConfirm(null);
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const revoke = useMutation({
    mutationFn: () => endpoints.userRevoke(telegramId),
    onSuccess: (res) => {
      setConfirm(null);
      if (res.ok) {
        toast.success(`${name}: доступ отозван`);
      } else {
        // ok=false означает «отзывать было нечего». Молчать про это нельзя:
        // админ решит, что доступ снят, а его и не было.
        toast.info(`${name}: отзывать было нечего — действующей подписки нет`);
      }
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const vip = useMutation({
    mutationFn: (grantIt: boolean) =>
      grantIt ? endpoints.userVipGrant(telegramId) : endpoints.userVipRevoke(telegramId),
    onSuccess: (_res, grantIt) => {
      setConfirm(null);
      toast.success(grantIt ? `${name}: VIP выдан` : `${name}: VIP снят`);
      onChanged();
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  const remove = useMutation({
    mutationFn: () => endpoints.userDelete(telegramId),
    onSuccess: () => {
      toast.success(`${name} удалён. История платежей сохранена.`);
      // Карточки больше не существует — уходим на чистый список.
      window.location.assign("/dashboard/users");
    },
    onError: (e) => {
      setConfirm(null);
      fail(e);
    },
  });

  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase tracking-[0.06em] text-fg-subtle">
        Доступ
      </div>

      {/* Выдача и продление. Быстрые кнопки закрывают девять случаев из
          десяти, поле рядом — остальные. */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {QUICK_DAYS.map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => setDays(d)}
            aria-pressed={days === d}
            className={
              days === d
                ? "rounded-md border border-accent-7 bg-accent-3 px-2 py-1 text-xs font-medium text-accent-text"
                : "rounded-md border border-border-control px-2 py-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            }
          >
            {d} дн
          </button>
        ))}
        <div className="w-24">
          <Input
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
            aria-label="Сколько дней выдать"
          />
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select
          value={tariff}
          onChange={(e) => setTariff(e.target.value)}
          aria-label="Тариф"
          className="h-9 rounded-md border border-border-control bg-bg-card px-2 text-base text-fg"
        >
          {TARIFFS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <Button
          variant="primary"
          icon={<KeyRound className="h-3.5 w-3.5" />}
          loading={grant.isPending}
          onClick={() => grant.mutate()}
        >
          Выдать {days} дн
        </Button>
        {sub && (
          <Button onClick={() => setConfirm("switch")}>Сменить тариф</Button>
        )}
      </div>
      <p className="mt-1.5 text-xs text-fg-muted">
        {sub
          ? `Дни прибавятся к текущему сроку (сейчас до ${fmtDate(expiresAt)}). Человек получит уведомление с ключом.`
          : "Подписки нет — будет создана новая. Человек получит уведомление с ключом."}
      </p>

      <div className="mt-4 flex flex-wrap gap-2 border-t border-border-subtle pt-3">
        <Button
          variant="danger"
          icon={<ShieldOff className="h-3.5 w-3.5" />}
          onClick={() => setConfirm("revoke")}
          disabled={!sub}
        >
          Отозвать доступ
        </Button>
        <Button
          icon={<Crown className="h-3.5 w-3.5" />}
          onClick={() => setConfirm(data.is_vip ? "vip-revoke" : "vip-grant")}
        >
          {data.is_vip ? "Снять VIP" : "Выдать VIP"}
        </Button>
        <Button
          variant="ghost"
          icon={<Trash2 className="h-3.5 w-3.5" />}
          className="ml-auto text-danger hover:text-danger"
          onClick={() => setConfirm("delete")}
        >
          Удалить
        </Button>
      </div>

      <ConfirmDialog
        open={confirm === "revoke"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => revoke.mutate()}
        destructive
        loading={revoke.isPending}
        title={`Отозвать доступ у ${name}`}
        confirmLabel="Отозвать доступ"
        cancelLabel="Оставить доступ"
        body={
          <>
            Подписка {currentTariff ? <b>{currentTariff}</b> : null}
            {expiresAt ? <> до <b>{fmtDate(expiresAt)}</b></> : null} перестанет
            действовать, ключ в приложении отключится сразу.
            <div className="mt-2">
              Деньги при этом не возвращаются, и обратной кнопки нет: вернуть
              доступ можно только новой выдачей дней.
            </div>
          </>
        }
      />

      <ConfirmDialog
        open={confirm === "switch"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => switchTariff.mutate()}
        loading={switchTariff.isPending}
        title={`Сменить тариф у ${name}`}
        confirmLabel="Сменить тариф"
        body={
          <>
            Тариф действующей подписки станет{" "}
            <b>{TARIFFS.find((t) => t.value === tariff)?.label}</b>
            {currentTariff ? <> вместо <b>{currentTariff}</b></> : null}. Срок
            не изменится{expiresAt ? <>, доступ остаётся до {fmtDate(expiresAt)}</> : null}.
            <div className="mt-2">
              Комбо-тариф — отдельный продукт с пакетом ГБ обхода: переход на
              него сам по себе трафик не начисляет.
            </div>
          </>
        }
      />

      <ConfirmDialog
        open={confirm === "vip-grant"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => vip.mutate(true)}
        loading={vip.isPending}
        title={`Выдать VIP пользователю ${name}`}
        confirmLabel="Выдать VIP"
        body="VIP-статус даёт особые условия в боте и держится, пока его не снимут. На срок подписки он не влияет."
      />

      <ConfirmDialog
        open={confirm === "vip-revoke"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => vip.mutate(false)}
        destructive
        loading={vip.isPending}
        title={`Снять VIP с ${name}`}
        confirmLabel="Снять VIP"
        cancelLabel="Оставить VIP"
        body="Особые условия перестанут применяться при следующей покупке. Подписка и баланс не меняются."
      />

      <ConfirmDialog
        open={confirm === "delete"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => remove.mutate()}
        destructive
        loading={remove.isPending}
        title={`Удалить пользователя ${name}`}
        confirmLabel="Удалить навсегда"
        requireText={String(telegramId)}
        requireHint={`Введите telegram_id ${telegramId} — так нельзя удалить не того человека`}
        body={
          <>
            Сотрёт профиль, подписки, рефералов, гифты, VIP и скидки, а также
            удалит сущность в Remnawave. <b>Отменить это нельзя.</b>
            <div className="mt-2">
              История платежей останется: иначе выручка, ARPU и график по дням
              пересчитались бы задним числом. В журнал запишется, сколько строк
              и на какую сумму сохранено.
            </div>
          </>
        }
      />
    </Card>
  );
}
