import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  ShieldCheck,
  Crown,
  Wallet,
  Percent,
  Calendar,
  Hash,
  UserCircle2,
  RefreshCcw,
  Plus,
  Minus,
  Trash2,
  ChevronRight,
} from "lucide-react";
import { endpoints, ApiError, type UserDetail } from "@/lib/api";
import { fmtNum, fmtRub, fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

export function Users() {
  const [params] = useSearchParams();
  const initialTg = params.get("tg") || "";
  const [query, setQuery] = useState(initialTg);
  const [submitted, setSubmitted] = useState(initialTg);
  // After the search returns >1 match the admin picks one from the
  // list. After picking, we render the full card for that telegram_id.
  const [picked, setPicked] = useState<number | null>(
    initialTg && /^\d+$/.test(initialTg) ? Number(initialTg) : null,
  );

  // Re-trigger search if the ?tg=… changes (e.g. user clicks
  // multiple "Полная карточка" links from the payments feed).
  useEffect(() => {
    const tg = params.get("tg") || "";
    if (tg && tg !== submitted) {
      setQuery(tg);
      setSubmitted(tg);
      setPicked(/^\d+$/.test(tg) ? Number(tg) : null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const search = useQuery({
    queryKey: ["users", "search", submitted],
    queryFn: () => endpoints.userSearch(submitted),
    enabled: submitted.length > 0,
    retry: false,
  });

  const matches = search.data?.matches ?? [];

  // Auto-pick when search returns exactly one result so the admin
  // doesn't have to click again — same UX as the old single-result
  // endpoint. Multiple matches always need an explicit pick.
  useEffect(() => {
    if (!search.data) return;
    if (matches.length === 1) {
      setPicked(matches[0].telegram_id);
    } else if (matches.length === 0) {
      setPicked(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.data]);

  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Управление
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Пользователи
        </h1>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          const q = query.trim();
          setSubmitted(q);
          setPicked(null);
        }}
        className="card flex items-center gap-2 p-2"
      >
        <Search className="ml-2 h-4 w-4 text-fg-subtle" />
        <input
          className="flex-1 bg-transparent px-2 py-2 text-sm text-fg placeholder:text-fg-subtle outline-none"
          placeholder="Telegram ID, @username или часть имени"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          inputMode="search"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
        />
        <button type="submit" className="btn-primary" disabled={!query.trim()}>
          Найти
        </button>
      </form>

      {submitted && search.isLoading && (
        <div className="card flex items-center gap-3 p-6 text-sm text-fg-muted">
          <Spinner /> Ищу...
        </div>
      )}

      {submitted && search.isError && (
        <EmptyState
          icon={UserCircle2}
          title="Не удалось выполнить поиск"
          description={(search.error as ApiError)?.detail}
        />
      )}

      {submitted &&
        !search.isLoading &&
        !search.isError &&
        matches.length === 0 && (
          <EmptyState
            icon={UserCircle2}
            title="Никого не нашли"
            description={`По запросу «${submitted}» нет совпадений ни по telegram_id, ни по username.`}
          />
        )}

      {matches.length > 1 && picked === null && (
        <SearchResultsList
          query={submitted}
          matches={matches}
          onPick={setPicked}
        />
      )}

      {picked !== null && (
        <UserCard
          telegramId={picked}
          onBack={
            matches.length > 1 ? () => setPicked(null) : undefined
          }
        />
      )}
    </div>
  );
}

function SearchResultsList({
  query,
  matches,
  onPick,
}: {
  query: string;
  matches: Array<{
    telegram_id: number;
    username: string | null;
    language: string | null;
    created_at: string | null;
    has_active_sub: boolean;
  }>;
  onPick: (tg: number) => void;
}) {
  return (
    <div className="card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            Найдено
          </div>
          <h2 className="text-base font-semibold text-fg">
            {matches.length} совпадений по «{query}»
          </h2>
        </div>
      </div>
      <ul className="divide-y divide-border/60">
        {matches.map((m) => (
          <li key={m.telegram_id}>
            <button
              type="button"
              onClick={() => onPick(m.telegram_id)}
              className="flex w-full items-center gap-3 py-3 text-left transition-colors hover:bg-accent/[0.04]"
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border">
                <UserCircle2 className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-medium text-fg">
                    {m.username ? `@${m.username}` : `tg:${m.telegram_id}`}
                  </span>
                  {m.username && (
                    <span className="badge-muted font-mono text-[10px]">
                      tg:{m.telegram_id}
                    </span>
                  )}
                  {m.has_active_sub ? (
                    <span className="badge-success text-[10px]">
                      <ShieldCheck className="h-3 w-3" /> active
                    </span>
                  ) : (
                    <span className="badge-muted text-[10px]">no sub</span>
                  )}
                </div>
                {m.created_at && (
                  <div className="mt-0.5 text-xs text-fg-muted">
                    зарегистрирован {fmtDate(m.created_at)}
                  </div>
                )}
              </div>
              <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function UserCard({
  telegramId,
  onBack,
}: {
  telegramId: number;
  onBack?: () => void;
}) {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["users", "detail", telegramId],
    queryFn: () => endpoints.userDetail(telegramId),
  });

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["users", "detail", telegramId] });

  if (detail.isLoading) {
    return (
      <div className="card flex items-center gap-3 p-6 text-sm text-fg-muted">
        <Spinner /> Загружаю карточку...
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    return (
      <EmptyState
        icon={UserCircle2}
        title="Не удалось загрузить"
        description={(detail.error as ApiError)?.detail}
      />
    );
  }

  const d = detail.data;
  const u = d.user as Record<string, unknown>;
  const sub = d.subscription;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="card p-5 lg:col-span-2">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wider text-fg-subtle">Карточка</div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-fg">
                {typeof u.username === "string" && u.username
                  ? `@${u.username}`
                  : `tg:${telegramId}`}
              </h2>
              {typeof u.username === "string" && u.username && (
                <span className="badge-muted font-mono">tg:{telegramId}</span>
              )}
              {d.is_vip && (
                <span className="badge-warning">
                  <Crown className="h-3 w-3" /> VIP
                </span>
              )}
              {sub && (sub as Record<string, unknown>).status === "active" ? (
                <span className="badge-success">
                  <ShieldCheck className="h-3 w-3" /> active
                </span>
              ) : (
                <span className="badge-muted">no sub</span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-fg-muted">
              <span className="inline-flex items-center gap-1.5">
                <Hash className="h-3 w-3" /> {telegramId}
              </span>
              {typeof u.created_at === "string" && (
                <span className="inline-flex items-center gap-1.5">
                  <Calendar className="h-3 w-3" /> зарегистрирован {fmtDate(u.created_at)}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {onBack && (
              <button
                type="button"
                onClick={onBack}
                className="btn-ghost"
              >
                ← к списку
              </button>
            )}
            <button
              type="button"
              onClick={() => detail.refetch()}
              className="btn-ghost"
              aria-label="Обновить"
            >
              <RefreshCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Cell
            icon={Wallet}
            label="Баланс"
            value={fmtRub(d.balance_rubles)}
          />
          <Cell
            icon={ShieldCheck}
            label="Тариф"
            value={
              sub
                ? String((sub as Record<string, unknown>).subscription_type ?? "—")
                : "—"
            }
          />
          <Cell
            icon={Calendar}
            label="Истекает"
            value={fmtDate(
              sub ? String((sub as Record<string, unknown>).expires_at ?? "") : null,
            )}
          />
          <Cell
            icon={Percent}
            label="Скидка"
            value={
              d.discount
                ? `${String((d.discount as Record<string, unknown>).discount_percent ?? "—")}%`
                : "—"
            }
          />
        </div>

        <Actions telegramId={telegramId} detail={d} onChange={invalidate} />
      </div>

      <div className="space-y-4">
        <TrialCard detail={d} />
        <BalanceCard
          telegramId={telegramId}
          balance={d.balance_rubles}
          onChange={invalidate}
        />
        <DiscountCard
          telegramId={telegramId}
          detail={d}
          onChange={invalidate}
        />
        <TrafficDiscountCard
          telegramId={telegramId}
          detail={d}
          onChange={invalidate}
        />
      </div>

      <div className="lg:col-span-3">
        <PaymentsCard telegramId={telegramId} />
      </div>
    </div>
  );
}

function Cell({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Wallet;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/60 p-3">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-fg">{value}</div>
    </div>
  );
}

function TrialCard({ detail }: { detail: UserDetail }) {
  const t = detail.trial as Record<string, unknown> | null;
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wider text-fg-subtle">Триал</div>
      {!t ? (
        <div className="mt-2 text-sm text-fg-muted">Не использовался</div>
      ) : (
        <div className="mt-2 space-y-1.5 text-sm">
          <Row label="Активирован" value={fmtDate(t.trial_used_at as string)} />
          <Row label="Истекает" value={fmtDate(t.trial_expires_at as string)} />
        </div>
      )}
    </div>
  );
}

function DiscountCard({
  telegramId,
  detail,
  onChange,
}: {
  telegramId: number;
  detail: UserDetail;
  onChange: () => void;
}) {
  const [percent, setPercent] = useState(30);
  const [hours, setHours] = useState<number | "">(24);
  const create = useMutation({
    mutationFn: () =>
      endpoints.userDiscountCreate(telegramId, {
        percent,
        expires_in_hours: typeof hours === "number" ? hours : null,
      }),
    onSuccess: () => {
      toast.success("Скидка создана");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });
  const del = useMutation({
    mutationFn: () => endpoints.userDiscountDelete(telegramId),
    onSuccess: () => {
      toast.success("Скидка удалена");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const existing = detail.discount as Record<string, unknown> | null;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-fg-subtle">Персональная скидка</div>
        {existing && (
          <button
            type="button"
            onClick={() => del.mutate()}
            className="btn-ghost text-danger hover:text-danger"
            disabled={del.isPending}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {existing && (
        <div className="mt-2 rounded-lg bg-bg-elevated px-3 py-2 text-sm text-fg">
          <b>{String(existing.discount_percent ?? "—")}%</b> до{" "}
          {fmtDate(existing.expires_at as string) || "бессрочно"}
        </div>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <input
          className="input"
          type="number"
          min={1}
          max={100}
          value={percent}
          onChange={(e) => setPercent(Number(e.target.value) || 0)}
          placeholder="%"
        />
        <input
          className="input"
          type="number"
          min={1}
          value={hours}
          onChange={(e) =>
            setHours(e.target.value === "" ? "" : Number(e.target.value))
          }
          placeholder="часов"
        />
      </div>
      <button
        type="button"
        onClick={() => create.mutate()}
        className="btn-primary mt-2 w-full"
        disabled={create.isPending || percent < 1 || percent > 100}
      >
        {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
        Применить
      </button>
    </div>
  );
}

function TrafficDiscountCard({
  telegramId,
  detail,
  onChange,
}: {
  telegramId: number;
  detail: UserDetail;
  onChange: () => void;
}) {
  const [percent, setPercent] = useState(30);
  const [hours, setHours] = useState<number | "">(24);
  const create = useMutation({
    mutationFn: () =>
      endpoints.userTrafficDiscountCreate(telegramId, {
        percent,
        expires_in_hours: typeof hours === "number" ? hours : null,
      }),
    onSuccess: () => {
      toast.success("Скидка на GB создана");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });
  const del = useMutation({
    mutationFn: () => endpoints.userTrafficDiscountDelete(telegramId),
    onSuccess: () => {
      toast.success("Скидка на GB удалена");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const existing = detail.traffic_discount as Record<string, unknown> | null;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-fg-subtle">
          Скидка на GB (Обход)
        </div>
        {existing && (
          <button
            type="button"
            onClick={() => del.mutate()}
            className="btn-ghost text-danger hover:text-danger"
            disabled={del.isPending}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {existing && (
        <div className="mt-2 rounded-lg bg-bg-elevated px-3 py-2 text-sm text-fg">
          <b>{String(existing.discount_percent ?? "—")}%</b> до{" "}
          {fmtDate(existing.expires_at as string) || "бессрочно"}
        </div>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <input
          className="input"
          type="number"
          min={1}
          max={100}
          value={percent}
          onChange={(e) => setPercent(Number(e.target.value) || 0)}
          placeholder="%"
        />
        <input
          className="input"
          type="number"
          min={1}
          value={hours}
          onChange={(e) =>
            setHours(e.target.value === "" ? "" : Number(e.target.value))
          }
          placeholder="часов"
        />
      </div>
      <button
        type="button"
        onClick={() => create.mutate()}
        className="btn-primary mt-2 w-full"
        disabled={create.isPending || percent < 1 || percent > 100}
      >
        {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
        Применить
      </button>
    </div>
  );
}

function Actions({
  telegramId,
  detail,
  onChange,
}: {
  telegramId: number;
  detail: UserDetail;
  onChange: () => void;
}) {
  const [days, setDays] = useState(30);
  const [tariff, setTariff] = useState("basic");

  const grant = useMutation({
    mutationFn: () => endpoints.userGrant(telegramId, { days, tariff }),
    onSuccess: () => {
      toast.success(`Выдано ${days} дн (${tariff})`);
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const revoke = useMutation({
    mutationFn: () => endpoints.userRevoke(telegramId),
    onSuccess: () => {
      toast.success("Доступ отозван");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const vipGrant = useMutation({
    mutationFn: () => endpoints.userVipGrant(telegramId),
    onSuccess: () => {
      toast.success("VIP выдан");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const vipRevoke = useMutation({
    mutationFn: () => endpoints.userVipRevoke(telegramId),
    onSuccess: () => {
      toast.success("VIP снят");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <div className="mt-6 rounded-2xl border border-border bg-bg-subtle/40 p-4">
      <div className="text-xs uppercase tracking-wider text-fg-subtle">Действия</div>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_auto]">
        <label className="flex items-center gap-2 rounded-xl border border-border bg-bg-card px-3">
          <span className="text-xs text-fg-subtle">Дней</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
            className="w-full bg-transparent py-2 text-sm text-fg outline-none"
          />
        </label>
        <select
          value={tariff}
          onChange={(e) => setTariff(e.target.value)}
          className="input"
        >
          <option value="basic">Basic</option>
          <option value="plus">Plus</option>
        </select>
        <button
          type="button"
          onClick={() => grant.mutate()}
          className="btn-primary"
          disabled={grant.isPending}
        >
          {grant.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
          Выдать
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            if (confirm("Отозвать подписку?")) revoke.mutate();
          }}
          className="btn-danger"
          disabled={revoke.isPending}
        >
          <Minus className="h-3.5 w-3.5" /> Отозвать доступ
        </button>
        {detail.is_vip ? (
          <button
            type="button"
            onClick={() => vipRevoke.mutate()}
            className="btn-secondary"
            disabled={vipRevoke.isPending}
          >
            <Crown className="h-3.5 w-3.5" /> Снять VIP
          </button>
        ) : (
          <button
            type="button"
            onClick={() => vipGrant.mutate()}
            className="btn-secondary"
            disabled={vipGrant.isPending}
          >
            <Crown className="h-3.5 w-3.5" /> Выдать VIP
          </button>
        )}
      </div>

      <DeleteUserSection telegramId={telegramId} />
    </div>
  );
}

function DeleteUserSection({ telegramId }: { telegramId: number }) {
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");

  const del = useMutation({
    mutationFn: () => endpoints.userDelete(telegramId),
    onSuccess: () => {
      toast.success("Пользователь полностью удалён");
      // Hard-reload — карточка больше не существует.
      window.location.assign("/dashboard/users");
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось удалить"),
  });

  if (!confirming) {
    return (
      <div className="mt-6 flex items-center justify-end">
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="btn-ghost text-danger hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" /> Удалить пользователя
        </button>
      </div>
    );
  }

  const required = String(telegramId);
  const ok = typed === required;

  return (
    <div className="mt-6 rounded-2xl border border-danger/40 bg-danger/10 p-4">
      <div className="text-xs font-medium uppercase tracking-wider text-danger">
        Полное удаление
      </div>
      <div className="mt-1 text-sm text-fg">
        Каскадно сотрёт: подписки, платежи, баланс, рефералы, гифты, VIP,
        скидки — и удалит entity в Remnawave. <b>Это необратимо.</b>
      </div>
      <div className="mt-3">
        <div className="mb-1 text-xs text-fg-muted">
          Введи Telegram ID <span className="font-mono text-fg">{required}</span>{" "}
          для подтверждения:
        </div>
        <input
          className="input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={required}
        />
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            setConfirming(false);
            setTyped("");
          }}
          className="btn-ghost"
          disabled={del.isPending}
        >
          Отмена
        </button>
        <button
          type="button"
          onClick={() => del.mutate()}
          disabled={!ok || del.isPending}
          className="btn-danger"
        >
          {del.isPending ? <Spinner /> : <Trash2 className="h-3.5 w-3.5" />}
          Подтвердить удаление
        </button>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-fg-muted">{label}</span>
      <span className="font-medium text-fg">{value}</span>
    </div>
  );
}

function BalanceCard({
  telegramId,
  balance,
  onChange,
}: {
  telegramId: number;
  balance: number;
  onChange: () => void;
}) {
  const [delta, setDelta] = useState<number | "">("");
  const [reason, setReason] = useState("");

  const change = useMutation({
    mutationFn: () =>
      endpoints.userBalanceChange(telegramId, {
        delta_rubles: typeof delta === "number" ? delta : 0,
        reason: reason || undefined,
      }),
    onSuccess: (data) => {
      toast.success(
        `Баланс ${(typeof delta === "number" ? delta : 0) > 0 ? "пополнен" : "списан"}: ${fmtRub(
          data.new_balance_rubles,
        )}`,
      );
      setDelta("");
      setReason("");
      onChange();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-fg-subtle">Баланс</div>
        <span className="font-mono text-sm font-semibold text-fg">
          {fmtRub(balance)}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        <input
          className="input"
          type="number"
          step="0.01"
          placeholder="± рублей (плюс — начисление, минус — списание)"
          value={delta}
          onChange={(e) =>
            setDelta(e.target.value === "" ? "" : Number(e.target.value))
          }
        />
        <input
          className="input"
          type="text"
          maxLength={200}
          placeholder="Причина (необязательно)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => {
              if (typeof delta !== "number" || delta === 0) return;
              change.mutate();
            }}
            disabled={
              change.isPending || typeof delta !== "number" || delta <= 0
            }
            className="btn-primary"
          >
            <Plus className="h-3.5 w-3.5" /> Пополнить
          </button>
          <button
            type="button"
            onClick={() => {
              if (typeof delta !== "number" || delta === 0) return;
              const v = -Math.abs(delta);
              setDelta(v);
              window.setTimeout(() => change.mutate(), 0);
            }}
            disabled={
              change.isPending || typeof delta !== "number" || delta === 0
            }
            className="btn-secondary"
          >
            <Minus className="h-3.5 w-3.5" /> Списать
          </button>
        </div>
      </div>
    </div>
  );
}

function PaymentsCard({ telegramId }: { telegramId: number }) {
  const payments = useQuery({
    queryKey: ["users", "payments", telegramId],
    queryFn: () => endpoints.userPayments(telegramId, 100),
  });

  const rows = (payments.data ?? []) as PurchaseRow[];
  const paidTotal = rows
    .filter((p) => p.status === "paid")
    .reduce((s, p) => s + (p.price_rubles ?? 0), 0);
  const paidCount = rows.filter((p) => p.status === "paid").length;
  const pendingCount = rows.filter((p) => p.status === "pending").length;

  return (
    <div className="card p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            История покупок
          </div>
          <h3 className="text-base font-semibold text-fg">
            Все операции в боте
          </h3>
          <div className="mt-1 text-xs text-fg-muted">
            оплачено {paidCount} · потрачено {fmtRub(paidTotal)}
            {pendingCount > 0 && ` · в ожидании ${pendingCount}`}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {payments.isFetching && <Spinner />}
          <button
            type="button"
            onClick={() => payments.refetch()}
            className="btn-ghost"
            aria-label="Обновить"
          >
            <RefreshCcw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {payments.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Wallet}
          title="Покупок нет"
          description="Пользователь ещё ничего не покупал — ни подписку, ни traffic-паки, ни пополнение баланса."
        />
      ) : (
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                <th className="px-2 py-2 font-medium">Дата</th>
                <th className="px-2 py-2 font-medium">Что куплено</th>
                <th className="px-2 py-2 font-medium">Сумма</th>
                <th className="px-2 py-2 font-medium">Провайдер</th>
                <th className="px-2 py-2 font-medium">Промо</th>
                <th className="px-2 py-2 font-medium">Статус</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {rows.map((p) => (
                <tr
                  key={String(p.id ?? p.purchase_id ?? Math.random())}
                  className="hover:bg-accent/[0.04]"
                >
                  <td className="px-2 py-2 align-top text-fg-muted whitespace-nowrap">
                    {fmtDate(p.created_at)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    <div className="font-medium text-fg">
                      {purchaseLabel(p)}
                    </div>
                    {p.purchase_id && (
                      <div
                        className="mt-0.5 font-mono text-[10px] text-fg-subtle"
                        title={p.purchase_id}
                      >
                        {p.purchase_id.length > 22
                          ? p.purchase_id.slice(0, 22) + "…"
                          : p.purchase_id}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2 align-top font-mono text-fg whitespace-nowrap">
                    {typeof p.price_rubles === "number"
                      ? fmtRub(p.price_rubles)
                      : "—"}
                  </td>
                  <td className="px-2 py-2 align-top text-fg-muted whitespace-nowrap">
                    {providerLabel(p.payment_provider)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    {p.promo_code ? (
                      <span className="badge-accent font-mono text-[10px]">
                        {p.promo_code}
                      </span>
                    ) : (
                      <span className="text-fg-subtle">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2 align-top">
                    <PaymentStatus status={p.status ?? ""} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface PurchaseRow {
  id?: number;
  purchase_id?: string;
  tariff?: string | null;
  purchase_type?: string | null;
  period_days?: number | null;
  price_kopecks?: number | null;
  price_rubles?: number | null;
  status?: string;
  created_at?: string | null;
  expires_at?: string | null;
  promo_code?: string | null;
  is_combo?: boolean | null;
  country?: string | null;
  farm_plot_id?: number | null;
  payment_provider?: string | null;
  provider_invoice_id?: string | null;
}

const TARIFF_RU: Record<string, string> = {
  basic: "Basic",
  plus: "Plus",
  biz_starter: "Biz Starter",
  biz_team: "Biz Team",
  biz_business: "Biz Business",
  biz_pro: "Biz Pro",
  biz_enterprise: "Biz Enterprise",
  biz_ultimate: "Biz Ultimate",
};

function purchaseLabel(p: PurchaseRow): string {
  const t = p.purchase_type ?? "subscription";
  if (t === "subscription") {
    const tariff = p.tariff ? TARIFF_RU[p.tariff] ?? p.tariff : "Подписка";
    const period = p.period_days ? ` · ${p.period_days} дн` : "";
    const combo = p.is_combo ? " (комбо)" : "";
    return `${tariff}${period}${combo}`;
  }
  if (t === "traffic_pack") {
    return p.country
      ? `Traffic-пак · ${p.country.toUpperCase()}`
      : "Traffic-пак";
  }
  if (t === "balance_topup") return "Пополнение баланса";
  if (t === "telegram_premium") return "Telegram Premium";
  if (t === "steam") return "Steam пополнение";
  if (t === "proxy") return "Прокси";
  if (t === "farm_plot") {
    return p.farm_plot_id
      ? `Фарм-участок #${p.farm_plot_id}`
      : "Фарм-участок";
  }
  return t;
}

const PROVIDER_RU: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Stars",
  lava: "Lava",
  balance: "С баланса",
  unknown: "—",
};

function providerLabel(p: string | null | undefined): string {
  if (!p) return "—";
  return PROVIDER_RU[p] ?? p;
}

function PaymentStatus({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === "approved" || s === "paid")
    return <span className="badge-success">оплачено</span>;
  if (s === "pending" || s === "processing")
    return <span className="badge-warning">ожидает</span>;
  if (s === "expired")
    return <span className="badge-muted">истёк</span>;
  if (s === "failed" || s === "rejected" || s === "cancelled")
    return <span className="badge-danger">{status}</span>;
  return <span className="badge-muted">{status || "—"}</span>;
}
