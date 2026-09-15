/**
 * Write actions on a subscription: grant, switch tariff, revoke.
 *
 * Two capabilities here were already built on the backend and had never
 * been reachable from the panel. `grant-minutes` exists so an operator can
 * verify the whole activation flow end to end without burning a day of
 * real access, and `switch-tariff` moves someone between basic and plus
 * without tearing down and reissuing their keys — which, done the long
 * way, changes the user's connection details for no reason.
 *
 * Destructive actions confirm inline rather than through `confirm()`. The
 * native dialog is unstyleable, announces poorly to a screen reader, and
 * was applied inconsistently anyway. (VIP was removed on 2026-09-14.)
 */
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Surface } from "@/components/ui/Surface";
import { Segmented } from "@/components/ui/controls";
import type { UserSubscription } from "@/types/user";

const TARIFFS = [
  { value: "basic", label: "Basic" },
  { value: "plus", label: "Plus" },
];

const QUICK_DAYS = [7, 30, 90, 365].map((d) => ({ value: d, label: `${d} дн` }));

interface Props {
  subscription: UserSubscription | null;
  onGrant: (days: number, tariff: string) => void;
  onGrantMinutes: (minutes: number) => void;
  onSwitchTariff: (tariff: string) => void;
  onRevoke: () => void;
  onReissueAggregator: () => void;
  onRefreshSubLinks: () => void;
  onReissueSubLinks: () => void;
  isPending?: boolean;
  className?: string;
}

export function UserActions({
  subscription,
  onGrant,
  onGrantMinutes,
  onSwitchTariff,
  onRevoke,
  onReissueAggregator,
  onRefreshSubLinks,
  onReissueSubLinks,
  isPending = false,
  className,
}: Props) {
  const [days, setDays] = useState(30);
  const [tariff, setTariff] = useState("basic");
  const [minutes, setMinutes] = useState(10);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [confirmReissue, setConfirmReissue] = useState(false);
  const [confirmSubReissue, setConfirmSubReissue] = useState(false);
  const [showTest, setShowTest] = useState(false);

  const current = subscription?.subscription_type ?? null;
  const otherTariff = current === "plus" ? "basic" : "plus";

  return (
    <Surface label="Действия" className={className}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <label htmlFor="grant-days" className="t-mute mb-1 block text-[12px]">
              Дней
            </label>
            <input
              id="grant-days"
              type="number"
              min={1}
              value={days}
              onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
              className="input tabular"
            />
          </div>
          <div className="min-w-0 flex-1">
            <label htmlFor="grant-tariff" className="t-mute mb-1 block text-[12px]">
              Тариф
            </label>
            <select
              id="grant-tariff"
              value={tariff}
              onChange={(e) => setTariff(e.target.value)}
              className="input"
            >
              {TARIFFS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn-primary"
            disabled={isPending}
            onClick={() => onGrant(days, tariff)}
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            Выдать
          </button>
        </div>

        {/* The common durations, so the usual case is one click rather
            than typing into a number field. */}
        <div className="-mx-1 max-w-full overflow-x-auto px-1 scrollbar-none">
          <Segmented label="Быстрый срок" value={days} options={QUICK_DAYS} onChange={setDays} />
        </div>

        {current && (
          <button
            type="button"
            className="btn-secondary"
            disabled={isPending}
            onClick={() => onSwitchTariff(otherTariff)}
          >
            Перевести на {otherTariff === "plus" ? "Plus" : "Basic"}
          </button>
        )}

        {/* Aggregator link reissue: new token, old link dies at once; the
            user has to take the new one from the bot. Only this user. */}
        {confirmReissue ? (
          <div className="flex flex-wrap items-center gap-2 rounded-row bg-tile-3 p-3">
            <span className="t-body min-w-0 flex-1 basis-40 text-[13px]">
              Перевыпустить ссылку агрегатора? Старая перестанет работать.
            </span>
            <button type="button" className="btn-ghost" onClick={() => setConfirmReissue(false)}>
              Отмена
            </button>
            <button
              type="button"
              className="btn-secondary bg-tile-4"
              disabled={isPending}
              onClick={() => {
                setConfirmReissue(false);
                onReissueAggregator();
              }}
            >
              Перевыпустить
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn-secondary w-full"
            disabled={isPending}
            onClick={() => setConfirmReissue(true)}
            title="Новый token агрегатора. Затрагивает только этого юзера, апстрим-ссылки не меняются."
          >
            Перевыпустить ссылку
          </button>
        )}

        {/* Subscription links (Premium + Обход). The bot serves them from its
            cache: after a manual «перевыпуск» in the panel, «Обновить»
            re-reads them; «Перевыпустить» does the panel revoke itself. */}
        <button
          type="button"
          className="btn-secondary w-full"
          disabled={isPending}
          onClick={onRefreshSubLinks}
          title="После ручного перевыпуска в панели: бот перечитает ссылки Premium и Обхода и сохранит новые."
        >
          Обновить ссылки из панели
        </button>

        {confirmSubReissue ? (
          <div className="flex flex-wrap items-center gap-2 rounded-row bg-tile-3 p-3">
            <span className="t-body min-w-0 flex-1 basis-40 text-[13px]">
              Перевыпустить подписку в панели? Старые ключи у пользователя перестанут работать —
              новый он возьмёт в боте.
            </span>
            <button type="button" className="btn-ghost" onClick={() => setConfirmSubReissue(false)}>
              Отмена
            </button>
            <button
              type="button"
              className="btn-danger"
              disabled={isPending}
              onClick={() => {
                setConfirmSubReissue(false);
                onReissueSubLinks();
              }}
            >
              Перевыпустить
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn-secondary w-full"
            disabled={isPending}
            onClick={() => setConfirmSubReissue(true)}
            title="Перевыпуск Premium и Обхода в панели: новые ссылки, старые перестают работать."
          >
            Перевыпустить подписку
          </button>
        )}

        {current &&
          (confirmRevoke ? (
            <div className="flex flex-wrap items-center gap-2 rounded-row bg-tile-3 p-3">
              <span className="t-body min-w-0 flex-1 text-[13px]">Отозвать доступ?</span>
              <button type="button" className="btn-ghost" onClick={() => setConfirmRevoke(false)}>
                Отмена
              </button>
              <button
                type="button"
                className="btn-danger"
                disabled={isPending}
                onClick={() => {
                  setConfirmRevoke(false);
                  onRevoke();
                }}
              >
                Отозвать
              </button>
            </div>
          ) : (
            <button type="button" className="btn-danger w-full" onClick={() => setConfirmRevoke(true)}>
              Отозвать доступ
            </button>
          ))}

        {/* Minute-level grants are a testing tool, so they stay folded
            away: an operator reaching for "выдать" should not have to
            step past a control that hands out ten minutes of access. */}
        <div>
          <button
            type="button"
            onClick={() => setShowTest((v) => !v)}
            aria-expanded={showTest}
            className="btn-ghost -ml-2 px-2 text-[13px]"
          >
            {showTest ? "Скрыть" : "Тестовая выдача в минутах"}
          </button>
          {showTest && (
            <div className="mt-2 flex items-end gap-2 rounded-row bg-tile-3 p-3">
              <div className="min-w-0 flex-1">
                <label htmlFor="grant-minutes" className="t-mute mb-1 block text-[12px]">
                  Минут
                </label>
                <input
                  id="grant-minutes"
                  type="number"
                  min={1}
                  value={minutes}
                  onChange={(e) => setMinutes(Math.max(1, Number(e.target.value) || 1))}
                  className="input tabular bg-tile-4"
                />
              </div>
              <button
                type="button"
                className="btn-secondary bg-tile-4"
                disabled={isPending}
                onClick={() => onGrantMinutes(minutes)}
              >
                Выдать
              </button>
            </div>
          )}
        </div>
      </div>
    </Surface>
  );
}
