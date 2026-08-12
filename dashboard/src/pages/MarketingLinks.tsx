import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link as LinkIcon,
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
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

type Tab = "stats" | "promo";

export function MarketingLinks() {
  const [tab, setTab] = useState<Tab>("stats");

  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Маркетинг
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Ссылки
        </h1>
        <p className="mt-2 max-w-xl text-sm text-fg-muted">
          <b>Статистика</b> — отслеживание переходов и воронки «клик → триал →
          покупка». <b>Промо</b> — выдача подписки / скидки / ГБ по одной
          ссылке. По 10 активных каждого типа.
        </p>
      </header>

      <div className="pill-tabs">
        <button
          type="button"
          onClick={() => setTab("stats")}
          className={tab === "stats" ? "pill-tab-active" : "pill-tab"}
        >
          <BarChart3 className="mr-1 h-3 w-3" />
          Статистика
        </button>
        <button
          type="button"
          onClick={() => setTab("promo")}
          className={tab === "promo" ? "pill-tab-active" : "pill-tab"}
        >
          <Gift className="mr-1 h-3 w-3" />
          Промо
        </button>
      </div>

      {tab === "stats" ? <StatsLinks /> : <PromoLinks />}
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
    <div className="space-y-4">
      <div className="card p-4">
        <div className="mb-3 text-xs uppercase tracking-wider text-fg-subtle">
          Создать stat-ссылку
        </div>
        <div className="flex flex-col gap-3 md:flex-row">
          <input
            className="input flex-1"
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
      </div>

      <div className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            Активные ссылки
          </div>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-ghost"
          >
            <RefreshCcw className="h-3.5 w-3.5" />
          </button>
        </div>
        {list.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            icon={LinkIcon}
            title="Пока пусто"
            description="Создай первую stat-ссылку — она будет писать клики и атрибуцию."
          />
        ) : (
          <div className="space-y-2">
            {list.data.map((raw) => {
              const l = raw as Record<string, unknown>;
              const id = Number(l.id);
              const url = String(l.t_me_url || "");
              const active = Boolean(l.is_active);
              return (
                <div
                  key={id}
                  className={
                    "rounded-xl border p-3 " +
                    (active
                      ? "border-border bg-bg-elevated/40"
                      : "border-border/50 bg-bg-subtle/40 opacity-70")
                  }
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-medium text-fg">
                        {String(l.name || "—")}
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(url);
                          toast.success("Ссылка скопирована");
                        }}
                        className="mt-1 inline-flex items-center gap-1.5 text-xs text-info hover:text-fg"
                        title="Скопировать"
                      >
                        <Copy className="h-3 w-3" />
                        <span className="truncate max-w-[240px] md:max-w-[420px]">
                          {url}
                        </span>
                      </button>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          toggle.mutate({ id, active: !active })
                        }
                        className="btn-ghost"
                        title={active ? "Деактивировать" : "Активировать"}
                      >
                        {active ? (
                          <PowerOff className="h-3.5 w-3.5" />
                        ) : (
                          <Power className="h-3.5 w-3.5 text-success" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm("Удалить ссылку и всю статистику?"))
                            del.mutate(id);
                        }}
                        className="btn-ghost text-danger"
                        title="Удалить"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-6">
                    <Metric icon={MousePointer2} label="Кликов" value={fmtNum(Number(l.total_clicks) || 0)} />
                    <Metric icon={UsersIcon} label="Уник." value={fmtNum(Number(l.unique_visitors) || 0)} />
                    <Metric icon={Plus} label="Новых" value={fmtNum(Number(l.new_users) || 0)} />
                    <Metric icon={Zap} label="Триалов" value={fmtNum(Number(l.trials_activated) || 0)} tone="info" />
                    <Metric icon={Gift} label="Купили" value={fmtNum(Number(l.paid_users) || 0)} tone="success" />
                    <Metric icon={BarChart3} label="Доход" value={fmtRub(Number(l.total_revenue_rubles) || 0)} tone="success" />
                  </div>
                  <div className="mt-2 text-[11px] text-fg-subtle">
                    Создана {fmtDate(String(l.created_at || ""))}
                    {!active && l.deactivated_at
                      ? ` · деактивирована ${fmtDate(String(l.deactivated_at))}`
                      : ""}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof BarChart3;
  label: string;
  value: string;
  tone?: "info" | "success";
}) {
  const cls =
    tone === "info"
      ? "text-info"
      : tone === "success"
      ? "text-success"
      : "text-fg";
  return (
    <div className="rounded-lg border border-border bg-bg-card px-2 py-1.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-fg-subtle">
        <Icon className="h-2.5 w-2.5" /> {label}
      </div>
      <div className={"mt-0.5 truncate text-sm font-semibold " + cls}>
        {value}
      </div>
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
    <div className="space-y-4">
      <div className="card p-4">
        <div className="mb-3 text-xs uppercase tracking-wider text-fg-subtle">
          Создать промо-ссылку
        </div>

        <input
          className="input mb-3"
          placeholder="Название (например: скидка новогодняя)"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />

        <div className="mb-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            Тип награды
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {(Object.keys(REWARD_LABELS) as RewardType[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => {
                  setRewardType(t);
                  ensureValidValue(t);
                }}
                className={
                  "rounded-xl border px-3 py-2 text-left text-sm transition " +
                  (rewardType === t
                    ? "border-accent/50 bg-accent/10 text-fg"
                    : "border-border bg-bg-card text-fg-muted hover:border-fg-subtle")
                }
              >
                {REWARD_LABELS[t]}
              </button>
            ))}
          </div>
        </div>

        <div className="mb-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            {rewardType === "subscription_days"
              ? "Срок подписки"
              : rewardType === "bypass_gb"
              ? "Гигабайты обхода"
              : "Процент скидки"}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {availableValues.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setRewardValue(v)}
                className={
                  "rounded-lg border px-3 py-1.5 text-sm transition " +
                  (rewardValue === v
                    ? "border-accent bg-accent text-bg"
                    : "border-border bg-bg-card text-fg-muted hover:border-fg-subtle")
                }
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
          <div className="mb-3">
            <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
              Тариф
            </div>
            <div className="flex gap-1.5">
              {(["basic", "plus"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTariff(t)}
                  className={
                    "rounded-lg border px-3 py-1.5 text-sm transition " +
                    (tariff === t
                      ? "border-accent bg-accent text-bg"
                      : "border-border bg-bg-card text-fg-muted hover:border-fg-subtle")
                  }
                >
                  {t === "basic" ? "Basic" : "Plus"}
                </button>
              ))}
            </div>
          </div>
        )}

        {(rewardType === "tariff_discount" ||
          rewardType === "bypass_discount") && (
          <div className="mb-3">
            <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
              Действует часов
            </div>
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
          </div>
        )}

        <div className="mb-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            Лимит использований (пусто = ∞)
          </div>
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
          <div className="mt-1 text-[11px] text-fg-subtle">
            Один пользователь может активировать эту ссылку только один раз.
          </div>
        </div>

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

      <div className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            Активные промо-ссылки
          </div>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-ghost"
          >
            <RefreshCcw className="h-3.5 w-3.5" />
          </button>
        </div>
        {list.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            icon={Gift}
            title="Пока пусто"
            description="Создай первую промо-ссылку — по клику пользователь получит награду."
          />
        ) : (
          <div className="space-y-2">
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
                <div
                  key={id}
                  className={
                    "rounded-xl border p-3 " +
                    (active
                      ? "border-border bg-bg-elevated/40"
                      : "border-border/50 bg-bg-subtle/40 opacity-70")
                  }
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-medium text-fg">
                        {String(l.name || "—")}
                      </div>
                      <div className="mt-0.5 text-xs text-fg-muted">
                        {REWARD_LABELS[rType]} · <b className="text-fg">{rewardLabel}</b>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(url);
                          toast.success("Ссылка скопирована");
                        }}
                        className="mt-1 inline-flex items-center gap-1.5 text-xs text-info hover:text-fg"
                      >
                        <Copy className="h-3 w-3" />
                        <span className="truncate max-w-[240px] md:max-w-[420px]">
                          {url}
                        </span>
                      </button>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          toggle.mutate({ id, active: !active })
                        }
                        className="btn-ghost"
                        title={active ? "Деактивировать" : "Активировать"}
                      >
                        {active ? (
                          <PowerOff className="h-3.5 w-3.5" />
                        ) : (
                          <Power className="h-3.5 w-3.5 text-success" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm("Удалить промо-ссылку?")) del.mutate(id);
                        }}
                        className="btn-ghost text-danger"
                        title="Удалить"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle">
                    <span className="badge-muted">
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
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
