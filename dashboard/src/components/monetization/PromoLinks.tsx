import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import {
  ApiError,
  endpoints,
  type PromoLinkReward,
  type PromoLinkRow,
} from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  EmptyFailure,
  EmptyFirstRun,
  Input,
  LoadingGate,
  SkeletonCard,
  StatusBadge,
} from "@/components/ui";
import { CopyField } from "./CopyField";
import { UsageMeter } from "./UsageMeter";
import { REWARD_LABEL, linkState, rewardSummary, tariffLabel } from "./labels";

/**
 * Промо-ссылки: переход по ссылке выдаёт награду.
 *
 * ЗНАЧЕНИЯ НАГРАД — БЕЛЫЕ СПИСКИ, И ЭТО НЕ КАПРИЗ. Сервер проверяет
 * reward_value по тем же наборам (database.VALID_SUB_DAYS и др.) и
 * отвечает 400 на всё остальное. Наборы ниже обязаны совпадать с
 * серверными: разойдутся — форма начнёт предлагать значения, которые не
 * сохраняются.
 *
 * ТАРИФ ТОЛЬКО BASIC ИЛИ PLUS. Обработчик диплинка приводит любой другой
 * тариф к basic (app/handlers/user/start/marketing_links.py). Комбо
 * предлагать нельзя: человек выбрал бы «Комбо Плюс», а покупатель получил
 * бы «Базовый», и расхождение всплыло бы только в жалобе.
 *
 * ЛИМИТ АКТИВНЫХ — тот же, что у статистических: десять. Показан рядом с
 * кнопкой, а не в тексте отказа после отправки формы.
 */

const MAX_ACTIVE = 10;

const SUB_DAYS = [3, 7, 14, 30, 90, 180, 365];
const DISCOUNT_PCTS = [10, 15, 20, 25, 30, 35, 40, 45, 50];
const BYPASS_GB = [5, 10, 15, 20, 25, 30, 50, 100];

function valuesFor(type: PromoLinkReward): number[] {
  if (type === "subscription_days") return SUB_DAYS;
  if (type === "bypass_gb") return BYPASS_GB;
  return DISCOUNT_PCTS;
}

function valueLabel(type: PromoLinkReward, v: number): string {
  if (type === "subscription_days") return v >= 30 ? `${Math.round(v / 30)} мес.` : `${v} дн.`;
  if (type === "bypass_gb") return `${v} ГБ`;
  return `−${v}%`;
}

export function PromoLinks() {
  const qc = useQueryClient();

  const list = useQuery({
    queryKey: ["links", "promo"],
    queryFn: endpoints.promoLinksList,
    refetchInterval: 60_000,
  });

  const rows = list.data ?? [];
  const activeCount = rows.filter((r) => r.is_active).length;
  const limitReached = activeCount >= MAX_ACTIVE;

  const [name, setName] = useState("");
  const [type, setType] = useState<PromoLinkReward>("subscription_days");
  const [value, setValue] = useState<number>(SUB_DAYS[0]);
  const [tariff, setTariff] = useState<"basic" | "plus">("basic");
  const [hours, setHours] = useState("24");
  const [maxUses, setMaxUses] = useState("100");

  const hoursNum = Number(hours.trim());
  const hoursValid = Number.isInteger(hoursNum) && hoursNum >= 1 && hoursNum <= 24 * 365;
  const hoursNeeded = type === "tariff_discount" || type === "bypass_discount";
  const hoursError = !hoursNeeded || hours.trim() === "" || hoursValid
    ? undefined
    : "Целое число часов, от 1 до 8760";

  // Пустое поле — сознательный выбор «без ограничения», а не забывчивость.
  const usesUnlimited = maxUses.trim() === "";
  const usesNum = Number(maxUses.trim());
  const usesValid = usesUnlimited || (Number.isInteger(usesNum) && usesNum >= 1 && usesNum <= 1_000_000);
  const usesError = usesValid ? undefined : "Целое от 1 до 1 000 000 либо пусто";

  const nameValid = name.trim().length > 0 && name.trim().length <= 80;
  const ready = nameValid && usesValid && (!hoursNeeded || hoursValid) && !limitReached;

  const changeType = (next: PromoLinkReward) => {
    setType(next);
    const allowed = valuesFor(next);
    if (!allowed.includes(value)) setValue(allowed[0]);
  };

  const create = useMutation({
    mutationFn: () => {
      const meta: Record<string, unknown> = {};
      if (type === "subscription_days") meta.tariff = tariff;
      if (hoursNeeded) meta.hours = hoursNum;
      return endpoints.promoLinkCreate({
        name: name.trim(),
        reward_type: type,
        reward_value: value,
        max_uses_total: usesUnlimited ? null : usesNum,
        max_uses_per_user: 1,
        reward_meta: meta,
      });
    },
    onSuccess: () => {
      toast.success("Промо-ссылка создана — скопируйте её из списка");
      setName("");
      qc.invalidateQueries({ queryKey: ["links", "promo"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось создать промо-ссылку"),
  });

  // Предпросмотр награды теми же словами, какими она подписана в списке.
  const preview = rewardSummary({
    reward_type: type,
    reward_value: value,
    reward_meta: type === "subscription_days" ? { tariff } : hoursNeeded ? { hours: hoursNum } : {},
  } as PromoLinkRow);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Новая промо-ссылка"
          subtitle={`активных ${activeCount} из ${MAX_ACTIVE} · один человек активирует ссылку один раз`}
        />
        <CardBody className="space-y-3">
          <Input
            label="Название"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            placeholder="Новогодняя раздача"
            hint="Видно только вам"
            disabled={limitReached}
          />

          <fieldset disabled={limitReached}>
            <legend className="text-xs font-medium text-fg-muted">Что получит человек</legend>
            <div className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(Object.keys(REWARD_LABEL) as PromoLinkReward[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => changeType(t)}
                  aria-pressed={type === t}
                  className={cn(
                    "rounded-md border px-3 py-2 text-left text-base transition-colors",
                    type === t
                      ? "border-accent-9 bg-accent-3 font-medium text-fg"
                      : "border-border bg-bg-card text-fg-muted hover:text-fg",
                  )}
                >
                  {REWARD_LABEL[t]}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset disabled={limitReached}>
            <legend className="text-xs font-medium text-fg-muted">
              {type === "subscription_days"
                ? "Срок подписки"
                : type === "bypass_gb"
                  ? "Сколько гигабайт"
                  : "Размер скидки"}
            </legend>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {valuesFor(type).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setValue(v)}
                  aria-pressed={value === v}
                  className={cn(
                    "min-h-tap rounded-md border px-3 py-1.5 text-base transition-colors",
                    value === v
                      ? "border-accent-9 bg-accent-3 font-medium text-fg"
                      : "border-border bg-bg-card text-fg-muted hover:text-fg",
                  )}
                >
                  {valueLabel(type, v)}
                </button>
              ))}
            </div>
          </fieldset>

          {type === "subscription_days" && (
            <fieldset disabled={limitReached}>
              <legend className="text-xs font-medium text-fg-muted">Какой тариф выдать</legend>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {(["basic", "plus"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTariff(t)}
                    aria-pressed={tariff === t}
                    className={cn(
                      "min-h-tap rounded-md border px-3 py-1.5 text-base transition-colors",
                      tariff === t
                        ? "border-accent-9 bg-accent-3 font-medium text-fg"
                        : "border-border bg-bg-card text-fg-muted hover:text-fg",
                    )}
                  >
                    {tariffLabel(t)}
                  </button>
                ))}
              </div>
              <p className="mt-1 text-xs text-fg-subtle">
                Комбо так выдать нельзя: обработчик ссылки понимает только эти
                два тарифа и любой другой молча заменит на «Базовый».
              </p>
            </fieldset>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            {hoursNeeded && (
              <Input
                label="Сколько часов действует скидка"
                value={hours}
                onChange={(e) => setHours(e.target.value)}
                inputMode="numeric"
                error={hoursError}
                disabled={limitReached}
              />
            )}
            <Input
              label="Максимум активаций"
              value={maxUses}
              onChange={(e) => setMaxUses(e.target.value)}
              inputMode="numeric"
              error={usesError}
              hint={usesError ? undefined : "Пусто — без ограничения"}
              disabled={limitReached}
            />
          </div>

          <div className="rounded-md border border-border bg-bg-subtle p-3 text-base text-fg-muted">
            {limitReached ? (
              <>
                Лимит активных промо-ссылок выбран ({MAX_ACTIVE}). Отключите
                ненужную в списке ниже — счётчик активаций при этом сохранится.
              </>
            ) : ready ? (
              <>
                Каждый, кто перейдёт по ссылке, получит{" "}
                <span className="font-medium text-fg">{preview}</span>
                {usesUnlimited
                  ? " — без ограничения по числу активаций."
                  : ` — пока ссылку не активируют ${usesNum} ${usesNum === 1 ? "раз" : "раз"}.`}
              </>
            ) : (
              "Заполните название — здесь появится, что именно получит человек по ссылке."
            )}
          </div>

          <div className="flex justify-end">
            <Button
              variant="primary"
              icon={<Plus className="h-3.5 w-3.5" aria-hidden />}
              disabled={!ready}
              loading={create.isPending}
              onClick={() => create.mutate()}
            >
              Создать ссылку
            </Button>
          </div>
        </CardBody>
      </Card>

      {list.isError ? (
        <EmptyFailure
          what="список промо-ссылок"
          reason="Список не пришёл. Пустой экран здесь читался бы как «ссылок нет» — это не так, это отказ запроса."
          onRetry={() => list.refetch()}
        />
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={
            <div className="space-y-3">
              <SkeletonCard lines={3} />
              <SkeletonCard lines={3} />
            </div>
          }
          message="Считаю активации промо-ссылок"
        >
          {list.data && rows.length === 0 ? (
            <EmptyFirstRun
              title="Промо-ссылок ещё нет"
              description="По такой ссылке человек сразу получает награду: подписку на несколько дней, скидку на время или гигабайты обхода. Один человек может активировать одну ссылку только раз."
            />
          ) : (
            <div className="space-y-3">
              {rows.map((row) => (
                <PromoLinkCard key={row.id} row={row} />
              ))}
            </div>
          )}
        </LoadingGate>
      )}
    </div>
  );
}

function PromoLinkCard({ row }: { row: PromoLinkRow }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const state = linkState(row);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["links", "promo"] });

  const toggle = useMutation({
    mutationFn: () =>
      row.is_active
        ? endpoints.promoLinkDeactivate(row.id)
        : endpoints.promoLinkReactivate(row.id),
    onSuccess: () => {
      toast.success(
        row.is_active
          ? `«${row.name ?? row.slug}» отключена — награда больше не выдаётся`
          : `«${row.name ?? row.slug}» снова выдаёт награду`,
      );
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось переключить ссылку"),
  });

  const del = useMutation({
    mutationFn: () => endpoints.promoLinkDelete(row.id),
    onSuccess: () => {
      toast.success(`«${row.name ?? row.slug}» удалена`);
      setConfirmDelete(false);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось удалить ссылку"),
  });

  return (
    <Card className={cn(!row.is_active && "opacity-80")}>
      <CardHeader
        title={row.name || row.slug}
        subtitle={
          <>
            {REWARD_LABEL[row.reward_type]} · <b className="text-fg">{rewardSummary(row)}</b> ·
            создана {fmtDate(row.created_at)}
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
            <Button size="sm" onClick={() => toggle.mutate()} loading={toggle.isPending}>
              {row.is_active ? "Отключить" : "Включить"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}>
              Удалить
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-3">
        <CopyField value={row.t_me_url} label="ссылку" />
        <UsageMeter
          used={row.used_count}
          max={row.max_uses_total}
          noun="активаций"
          expiresAt={row.expires_at}
          className="max-w-sm"
        />
      </CardBody>

      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => del.mutate()}
        loading={del.isPending}
        destructive
        title={`Удалить промо-ссылку «${row.name ?? row.slug}»`}
        confirmLabel="Удалить ссылку"
        cancelLabel="Не удалять"
        requireText={row.name || row.slug}
        requireHint={`Наберите название ссылки — «${row.name || row.slug}»`}
        body={
          <>
            Ссылка перестанет работать у всех, кому она разослана. Награда
            «{rewardSummary(row)}» больше не выдаётся; уже выданное остаётся у
            людей. Активаций к этому моменту: {row.used_count ?? 0}.
            <div className="mt-2">
              Если нужно просто прекратить выдачу — отключите ссылку, счётчик
              активаций при этом сохранится.
            </div>
          </>
        }
      />
    </Card>
  );
}
