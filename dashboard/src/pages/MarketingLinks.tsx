import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  Gift,
  Plus,
  Copy,
  Trash2,
  Power,
  PowerOff,
  RefreshCcw,
  Users as UsersIcon,
  MousePointer2,
  Zap,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { toast } from "@/store/toast";
import { fmtNum, fmtRub, fmtDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/Spinner";
import { PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton, Segmented, type SegmentedOption } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

type Tab = "stats" | "promo";

const TABS: SegmentedOption<Tab>[] = [
  { value: "stats", label: "Статистика" },
  { value: "promo", label: "Промо" },
];

export function MarketingLinks() {
  const [tab, setTab] = useState<Tab>("stats");

  return (
    <div>
      <PageHeader
        title="Ссылки"
        sub={
          <>
            <b className="font-semibold">Статистика</b> — отслеживание переходов и воронки «клик → триал →
            покупка». <b className="font-semibold">Промо</b> — выдача подписки / скидки / ГБ по одной
            ссылке. По 10 активных каждого типа.
          </>
        }
        actions={<Segmented label="Тип ссылок" value={tab} options={TABS} onChange={setTab} />}
      />

      {tab === "stats" ? <StatsLinks /> : <PromoLinks />}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// Shared bits
// ══════════════════════════════════════════════════════════════════════

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
      <Skeleton className="h-[120px] w-full rounded-row" />
      <Skeleton className="h-[120px] w-full rounded-row" />
    </div>
  );
}

function CopyUrl({ url }: { url: string }) {
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(url);
        toast.success("Ссылка скопирована");
      }}
      className="tap-target t-body mt-1 inline-flex max-w-full items-center gap-1.5 text-[13px] hover:text-ink"
      title="Скопировать"
      aria-label={`Скопировать ссылку ${url}`}
    >
      <Copy className="h-3 w-3 flex-none" aria-hidden="true" />
      <span className="max-w-[240px] truncate font-mono text-[12px] md:max-w-[420px]">{url}</span>
    </button>
  );
}

function RowActions({
  active,
  onToggle,
  onDelete,
}: {
  active: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <IconButton small label={active ? "Деактивировать" : "Активировать"} onClick={onToggle}>
        {active ? <PowerOff className="h-3.5 w-3.5" /> : <Power className="h-3.5 w-3.5 text-success" />}
      </IconButton>
      <IconButton small label="Удалить" onClick={onDelete} className="text-danger">
        <Trash2 className="h-3.5 w-3.5" />
      </IconButton>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// STATS LINKS
// ══════════════════════════════════════════════════════════════════════

function StatsLinks() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["links", "stats"],
    queryFn: endpoints.statsLinksList,
    refetchInterval: 30_000,
  });

  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => endpoints.statsLinkCreate({ name: name.trim() }),
    onSuccess: () => {
      toast.success("Stat-ссылка создана");
      setName("");
      qc.invalidateQueries({ queryKey: ["links", "stats"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const del = useMutation({
    mutationFn: (id: number) => endpoints.statsLinkDelete(id),
    onSuccess: () => {
      toast.success("Удалено");
      qc.invalidateQueries({ queryKey: ["links", "stats"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      active
        ? endpoints.statsLinkReactivate(id)
        : endpoints.statsLinkDeactivate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["links", "stats"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      <Surface variant="raised" label="Создать stat-ссылку">
        <div className="flex flex-col gap-3 md:flex-row">
          <input
            className="input flex-1"
            aria-label="Название ссылки"
            placeholder="Название (например: инста-пост декабрь)"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            className="btn-primary"
            onClick={() => create.mutate()}
            disabled={create.isPending || name.trim().length === 0}
          >
            {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
            Создать
          </button>
        </div>
      </Surface>

      <Surface
        label="Активные ссылки"
        aside={
          <IconButton small label="Обновить" onClick={() => list.refetch()} className="bg-tile-3">
            <RefreshCcw className="h-3.5 w-3.5" />
          </IconButton>
        }
      >
        {list.isLoading ? (
          <ListSkeleton />
        ) : list.isError && !Array.isArray(list.data) ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} className="bg-tile-3" />
        ) : /* Array.isArray, not a truthiness check: a non-array payload
               passed this guard and reached .map() below, which white-
               screened the page instead of showing the empty state. */
        !Array.isArray(list.data) || list.data.length === 0 ? (
          <EmptyState
            title="Пока пусто"
            hint="Создай первую stat-ссылку — она будет писать клики и атрибуцию."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {list.data.map((raw) => {
              const l = raw as Record<string, unknown>;
              const id = Number(l.id);
              const url = String(l.t_me_url || "");
              const active = Boolean(l.is_active);
              return (
                <li
                  key={id}
                  className={cn("rounded-row bg-tile-3 p-4", !active && "opacity-70")}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold">
                        {String(l.name || "—")}
                      </div>
                      <CopyUrl url={url} />
                    </div>
                    <RowActions
                      active={active}
                      onToggle={() => toggle.mutate({ id, active: !active })}
                      onDelete={() => {
                        if (confirm("Удалить ссылку и всю статистику?"))
                          del.mutate(id);
                      }}
                    />
                  </div>

                  <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
                    <Metric icon={MousePointer2} label="Кликов" value={fmtNum(Number(l.total_clicks) || 0)} />
                    <Metric icon={UsersIcon} label="Уник." value={fmtNum(Number(l.unique_visitors) || 0)} />
                    <Metric icon={Plus} label="Новых" value={fmtNum(Number(l.new_users) || 0)} />
                    <Metric icon={Zap} label="Триалов" value={fmtNum(Number(l.trials_activated) || 0)} />
                    <Metric icon={Gift} label="Купили" value={fmtNum(Number(l.paid_users) || 0)} />
                    <Metric icon={BarChart3} label="Доход" value={fmtRub(Number(l.total_revenue_rubles) || 0)} />
                  </div>
                  <div className="t-mute mt-3 text-[12px]">
                    Создана {fmtDate(String(l.created_at || ""))}
                    {!active && l.deactivated_at
                      ? ` · деактивирована ${fmtDate(String(l.deactivated_at))}`
                      : ""}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Surface>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof BarChart3;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-row bg-tile-1 px-3 py-2">
      <div className="t-mute flex items-center gap-1.5 text-[12px]">
        <Icon className="h-3 w-3" aria-hidden="true" /> {label}
      </div>
      <div className="tabular mt-0.5 truncate text-[14px] font-semibold">{value}</div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// PROMO LINKS
// ══════════════════════════════════════════════════════════════════════

type RewardType =
  | "subscription_days"
  | "tariff_discount"
  | "bypass_discount"
  | "bypass_gb";

const SUB_DAYS = [3, 7, 14, 30, 90, 180, 365] as const;
const DISCOUNT_PCTS = [10, 15, 20, 25, 30, 35, 40, 45, 50] as const;
const BYPASS_GB_VALUES = [5, 10, 15, 20, 25, 30, 50, 100] as const;

const REWARD_LABELS: Record<RewardType, string> = {
  subscription_days: "Выдача подписки",
  tariff_discount: "Скидка на тарифы",
  bypass_discount: "Скидка на ГБ обхода",
  bypass_gb: "Выдача ГБ обхода",
};

const TARIFFS: SegmentedOption<"basic" | "plus">[] = [
  { value: "basic", label: "Basic" },
  { value: "plus", label: "Plus" },
];

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <div className="t-mute mb-2 text-[13px]">{children}</div>;
}

function PromoLinks() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["links", "promo"],
    queryFn: endpoints.promoLinksList,
    refetchInterval: 30_000,
  });

  const [name, setName] = useState("");
  const [rewardType, setRewardType] = useState<RewardType>("subscription_days");
  const [rewardValue, setRewardValue] = useState<number>(3);
  const [maxTotal, setMaxTotal] = useState<number | "">(100);
  const [tariff, setTariff] = useState<"basic" | "plus">("basic");
  const [hours, setHours] = useState<number | "">(24);

  const availableValues = (() => {
    if (rewardType === "subscription_days") return SUB_DAYS;
    if (rewardType === "bypass_gb") return BYPASS_GB_VALUES;
    return DISCOUNT_PCTS;
  })();

  // Синхронизируем текущее значение с whitelist'ом при смене типа
  const ensureValidValue = (t: RewardType) => {
    const allowed =
      t === "subscription_days"
        ? SUB_DAYS
        : t === "bypass_gb"
        ? BYPASS_GB_VALUES
        : DISCOUNT_PCTS;
    if (!allowed.includes(rewardValue as never)) {
      setRewardValue(allowed[0]);
    }
  };

  const create = useMutation({
    mutationFn: () => {
      const meta: Record<string, unknown> = {};
      if (rewardType === "subscription_days") meta.tariff = tariff;
      if (rewardType === "tariff_discount" || rewardType === "bypass_discount") {
        meta.hours = typeof hours === "number" ? hours : 24;
      }
      return endpoints.promoLinkCreate({
        name: name.trim(),
        reward_type: rewardType,
        reward_value: rewardValue,
        max_uses_total: typeof maxTotal === "number" ? maxTotal : null,
        max_uses_per_user: 1,
        reward_meta: meta,
      });
    },
    onSuccess: () => {
      toast.success("Промо-ссылка создана");
      setName("");
      qc.invalidateQueries({ queryKey: ["links", "promo"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const del = useMutation({
    mutationFn: (id: number) => endpoints.promoLinkDelete(id),
    onSuccess: () => {
      toast.success("Удалено");
      qc.invalidateQueries({ queryKey: ["links", "promo"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      active
        ? endpoints.promoLinkReactivate(id)
        : endpoints.promoLinkDeactivate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["links", "promo"] });
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      <Surface variant="raised" label="Создать промо-ссылку">
        <div className="flex flex-col gap-5">
          <input
            className="input"
            aria-label="Название промо-ссылки"
            placeholder="Название (например: скидка новогодняя)"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />

          <div>
            <FieldLabel>Тип награды</FieldLabel>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {(Object.keys(REWARD_LABELS) as RewardType[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={rewardType === t}
                  onClick={() => {
                    setRewardType(t);
                    ensureValidValue(t);
                  }}
                  className={cn(
                    "min-h-[44px] rounded-row px-4 py-2.5 text-left text-[14px] font-medium transition-colors",
                    rewardType === t
                      ? "bg-accent text-onaccent"
                      : "t-body bg-tile-3 hover:bg-tile-4 hover:text-ink",
                  )}
                >
                  {REWARD_LABELS[t]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <FieldLabel>
              {rewardType === "subscription_days"
                ? "Срок подписки"
                : rewardType === "bypass_gb"
                ? "Гигабайты обхода"
                : "Процент скидки"}
            </FieldLabel>
            <div className="flex flex-wrap gap-1.5">
              {availableValues.map((v) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={rewardValue === v}
                  onClick={() => setRewardValue(v)}
                  className="capsule-tab tabular bg-tile-3"
                >
                  {rewardType === "subscription_days"
                    ? v >= 30
                      ? `${Math.round(v / 30)} мес`
                      : `${v} дн`
                    : rewardType === "bypass_gb"
                    ? `${v} ГБ`
                    : `${v}%`}
                </button>
              ))}
            </div>
          </div>

          {rewardType === "subscription_days" && (
            <div>
              <FieldLabel>Тариф</FieldLabel>
              <Segmented label="Тариф" value={tariff} options={TARIFFS} onChange={setTariff} />
            </div>
          )}

          {(rewardType === "tariff_discount" ||
            rewardType === "bypass_discount") && (
            <label className="block">
              <FieldLabel>Действует часов</FieldLabel>
              <input
                type="number"
                className="input"
                min={1}
                max={24 * 365}
                value={hours}
                onChange={(e) =>
                  setHours(e.target.value === "" ? "" : Number(e.target.value))
                }
                placeholder="24"
              />
            </label>
          )}

          <label className="block">
            <FieldLabel>Лимит использований (пусто = ∞)</FieldLabel>
            <input
              type="number"
              className="input"
              min={1}
              max={1_000_000}
              value={maxTotal}
              onChange={(e) =>
                setMaxTotal(e.target.value === "" ? "" : Number(e.target.value))
              }
              placeholder="100"
            />
            <span className="t-mute mt-1.5 block text-[12px]">
              Один пользователь может активировать эту ссылку только один раз.
            </span>
          </label>

          <button
            type="button"
            className="btn-primary w-full"
            onClick={() => create.mutate()}
            disabled={create.isPending || name.trim().length === 0}
          >
            {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
            Создать промо-ссылку
          </button>
        </div>
      </Surface>

      <Surface
        label="Активные промо-ссылки"
        aside={
          <IconButton small label="Обновить" onClick={() => list.refetch()} className="bg-tile-3">
            <RefreshCcw className="h-3.5 w-3.5" />
          </IconButton>
        }
      >
        {list.isLoading ? (
          <ListSkeleton />
        ) : list.isError && !Array.isArray(list.data) ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} className="bg-tile-3" />
        ) : /* Array.isArray, not a truthiness check: a non-array payload
               passed this guard and reached .map() below, which white-
               screened the page instead of showing the empty state. */
        !Array.isArray(list.data) || list.data.length === 0 ? (
          <EmptyState
            title="Пока пусто"
            hint="Создай первую промо-ссылку — по клику пользователь получит награду."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {list.data.map((raw) => {
              const l = raw as Record<string, unknown>;
              const id = Number(l.id);
              const url = String(l.t_me_url || "");
              const active = Boolean(l.is_active);
              const rType = String(l.reward_type) as RewardType;
              const rVal = Number(l.reward_value) || 0;
              const rMeta = (l.reward_meta as Record<string, unknown>) || {};
              const rTariff = String(rMeta.tariff || "");
              const rHours = Number(rMeta.hours || 0);
              const usedCount = Number(l.used_count) || 0;
              const maxUsesTotal = l.max_uses_total as number | null;

              let rewardLabel = "";
              if (rType === "subscription_days") {
                rewardLabel = `${rVal} дн ${rTariff ? "· " + rTariff : ""}`;
              } else if (rType === "tariff_discount") {
                rewardLabel = `−${rVal}% на подписку${rHours ? " · " + rHours + "ч" : ""}`;
              } else if (rType === "bypass_discount") {
                rewardLabel = `−${rVal}% на ГБ${rHours ? " · " + rHours + "ч" : ""}`;
              } else if (rType === "bypass_gb") {
                rewardLabel = `+${rVal} ГБ обхода`;
              }

              return (
                <li
                  key={id}
                  className={cn("rounded-row bg-tile-3 p-4", !active && "opacity-70")}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold">
                        {String(l.name || "—")}
                      </div>
                      <div className="t-body mt-0.5 text-[13px]">
                        {REWARD_LABELS[rType]} · <b className="font-semibold text-ink">{rewardLabel}</b>
                      </div>
                      <CopyUrl url={url} />
                    </div>
                    <RowActions
                      active={active}
                      onToggle={() => toggle.mutate({ id, active: !active })}
                      onDelete={() => {
                        if (confirm("Удалить промо-ссылку?")) del.mutate(id);
                      }}
                    />
                  </div>

                  <div className="t-mute mt-3 flex flex-wrap items-center gap-2 text-[12px]">
                    <span className="badge-muted tabular bg-tile-1">
                      {fmtNum(usedCount)}
                      {maxUsesTotal ? ` / ${fmtNum(maxUsesTotal)}` : " / ∞"}{" "}
                      исп.
                    </span>
                    <span>· создана {fmtDate(String(l.created_at || ""))}</span>
                    {!active && Boolean(l.deactivated_at) && (
                      <span>
                        · деактивирована {fmtDate(String(l.deactivated_at))}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Surface>
    </div>
  );
}
