/**
 * Users — listing and detail.
 *
 * The screen used to be a search box and nothing else: with an empty
 * query it rendered a blank page, so the only answerable question was
 * "show me this person, whose id I already have". Everything about a
 * population — who signed up today, whose access lapses this week, who is
 * paying and has never connected — was unanswerable, and the endpoint to
 * answer it did not exist either.
 *
 * Now the default state is the population, and one user is a place you
 * navigate to. That navigation is a view transition: the row morphs into
 * the card heading, which keeps a reader's place in a list of hundreds.
 *
 * This file composes; it does not implement. The 1284-line version mixed
 * routing, presentation and domain vocabulary in one scope, which is how
 * it came to hold two different definitions of "paid".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowLeft, Download, Loader2, RefreshCw } from "lucide-react";

import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { IconButton, Segmented } from "@/components/ui/controls";
import { ErrorState } from "@/components/ui/states";
import { UsersFilters } from "@/components/users/list/UsersFilters";
import { UsersTable } from "@/components/users/list/UsersTable";
import { UserActions } from "@/components/users/UserActions";
import { UserDangerZone } from "@/components/users/UserDangerZone";
import { UserBalanceCard } from "@/components/users/UserBalanceCard";
import { UserTrialCard } from "@/components/users/UserTrialCard";
import { DiscountCard } from "@/components/users/DiscountCard";
import { CashbackFixCard } from "@/components/users/CashbackFixCard";
import { UserProfileStats } from "@/components/users/UserProfileStats";
import { UserPaymentsTable } from "@/components/users/UserPaymentsTable";
import { UserSubscriptionTechCard } from "@/components/users/UserSubscriptionTechCard";
import { UserTimeline } from "@/components/users/UserTimeline";
import { UserAuditLog } from "@/components/users/UserAuditLog";
import { CopyButton } from "@/components/users/shared/CopyButton";

import {
  useUserAudit,
  useUserDetail,
  useUserExtended,
  useUserHistory,
  useUserPayments,
  useUsersList,
} from "@/hooks/useUsers";
import { useUserMutations } from "@/hooks/useUserMutations";
import { downloadCsv } from "@/lib/api";
import { withViewTransition } from "@/lib/viewTransition";
import { daysUntil, fmtBytes, fmtDate, fmtRelative } from "@/lib/format";
import { isSubscriptionActive, tariffLabel } from "@/lib/userDomain";
import { usePrefs, type Density } from "@/store/prefs";
import { toast } from "@/store/toast";
import type { BypassState, PremiumState, UserListFilters } from "@/types/user";

const PAYMENTS_LIMIT = 100;
const PAGE_SIZE = 50;

const DENSITIES: { value: Density; label: string }[] = [
  { value: "compact", label: "Плотно" },
  { value: "comfortable", label: "Обычно" },
  { value: "spacious", label: "Просторно" },
];

/**
 * Filters live in the URL so a view can be linked to and survives a
 * reload. Booleans are the awkward part: absent and false mean different
 * things here ("any" versus "explicitly without"), so absence is only
 * ever expressed by the key not being present.
 */
function filtersFromParams(p: URLSearchParams): UserListFilters {
  const f: UserListFilters = { limit: PAGE_SIZE, offset: Number(p.get("offset")) || 0 };
  const q = p.get("q");
  if (q) f.q = q;
  if (p.has("has_sub")) f.has_sub = p.get("has_sub") === "true";
  const source = p.get("source");
  if (source) f.source = source;
  for (const k of ["created_after", "created_before", "expires_before"] as const) {
    const v = p.get(k);
    if (v) f[k] = v;
  }
  // Sorting is left unset while a search is running and the operator has
  // not picked a column: the backend ranks by relevance in exactly that
  // case (exact id, then exact username, then prefix, then substring),
  // and naming a sort column silently turns that off — the best match
  // would stop being the first row.
  //
  // Without a search there is no relevance to preserve, so the backend's
  // implicit created_at/desc is stated explicitly instead. Left implicit
  // the table draws no sort arrow, and an ordered listing that looks
  // unordered invites the reader to assume the top row means nothing.
  const sort = p.get("sort") as UserListFilters["sort"] | null;
  if (sort) {
    f.sort = sort;
    f.order = (p.get("order") as UserListFilters["order"]) ?? "desc";
  } else if (!f.q) {
    f.sort = "created_at";
    f.order = (p.get("order") as UserListFilters["order"]) ?? "desc";
  }
  return f;
}

function paramsFromFilters(f: UserListFilters, tg: number | null): URLSearchParams {
  const p = new URLSearchParams();
  if (tg !== null) p.set("tg", String(tg));
  for (const [k, v] of Object.entries(f)) {
    if (k === "limit") continue;
    if (k === "offset" && !v) continue;
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  return p;
}

export function Users() {
  const [params, setParams] = useSearchParams();

  const tgParam = params.get("tg");
  const tg = tgParam && /^\d+$/.test(tgParam) ? Number(tgParam) : null;

  const filters = useMemo(() => filtersFromParams(params), [params]);

  const setFilters = useCallback(
    (next: UserListFilters) => setParams(paramsFromFilters(next, tg), { replace: true }),
    [setParams, tg],
  );

  const open = useCallback(
    (id: number) =>
      withViewTransition(() => setParams(paramsFromFilters(filters, id))),
    [filters, setParams],
  );

  const back = useCallback(
    () => withViewTransition(() => setParams(paramsFromFilters(filters, null))),
    [filters, setParams],
  );

  return tg === null ? (
    <ListView filters={filters} setFilters={setFilters} onOpen={open} />
  ) : (
    <DetailView tg={tg} onBack={back} />
  );
}

/* ── Export ───────────────────────────────────────────────────────── */

function ExportUsersButton() {
  const [pending, setPending] = useState(false);
  async function run() {
    if (pending) return;
    setPending(true);
    try {
      await downloadCsv("/export/users.csv", `users_${new Date().toISOString().slice(0, 10)}.csv`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось выгрузить CSV");
    } finally {
      setPending(false);
    }
  }
  return (
    <button type="button" className="btn-secondary" onClick={run} disabled={pending} aria-busy={pending}>
      {pending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="h-4 w-4" aria-hidden="true" />
      )}
      {pending ? "Экспорт…" : "Экспорт CSV"}
    </button>
  );
}

/* ── Listing ──────────────────────────────────────────────────────── */

function ListView({
  filters,
  setFilters,
  onOpen,
}: {
  filters: UserListFilters;
  setFilters: (f: UserListFilters) => void;
  onOpen: (tg: number) => void;
}) {
  const list = useUsersList(filters);
  const density = usePrefs((s) => s.density);
  const setDensity = usePrefs((s) => s.setDensity);

  const hasFilters = Object.keys(filters).some(
    (k) => k !== "limit" && k !== "offset" && k !== "sort" && k !== "order",
  );

  const total = list.data?.total;
  const offset = filters.offset ?? 0;
  const shown = list.data?.rows?.length ?? 0;
  const canPrev = offset > 0;
  const canNext = total !== undefined && offset + shown < total;

  return (
    <>
      <PageHeader title="Пользователи" actions={<ExportUsersButton />} />

      <div className="flex flex-col gap-[var(--gap)]">
        <Surface label="Поиск и фильтры">
          <p className="t-mute mb-4 max-w-[72ch] text-[13px] leading-5">
            Пресеты отвечают на вопросы, которые задают чаще всего. Активные условия показаны
            чипами — отфильтрованный список не должен выглядеть как полный.
          </p>
          <UsersFilters
            value={filters}
            onChange={setFilters}
            total={total}
            isFetching={list.isFetching}
          />
        </Surface>

        <Surface
          label="Список"
          aside={
            <div className="-mx-1 max-w-full overflow-x-auto px-1 scrollbar-none">
              <Segmented label="Плотность таблицы" value={density} options={DENSITIES} onChange={setDensity} />
            </div>
          }
        >
          <UsersTable
            rows={list.data?.rows}
            isLoading={list.isLoading}
            isError={list.isError}
            filters={filters}
            onChange={setFilters}
            onOpen={onOpen}
            hasFilters={hasFilters}
          />

          {(canPrev || canNext) && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <span className="t-mute tabular text-[13px]">
                {offset + 1}–{offset + shown}
                {total !== undefined && ` из ${total}`}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={!canPrev}
                  onClick={() => setFilters({ ...filters, offset: Math.max(0, offset - PAGE_SIZE) })}
                >
                  Назад
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={!canNext}
                  onClick={() => setFilters({ ...filters, offset: offset + PAGE_SIZE })}
                >
                  Дальше
                </button>
              </div>
            </div>
          )}
        </Surface>
      </div>
    </>
  );
}

/* ── Detail ───────────────────────────────────────────────────────── */

function DetailView({ tg, onBack }: { tg: number; onBack: () => void }) {
  const detail = useUserDetail(tg);
  const extended = useUserExtended(tg);
  const payments = useUserPayments(tg, PAYMENTS_LIMIT);
  const history = useUserHistory(tg);
  const audit = useUserAudit(tg);
  const m = useUserMutations(tg);

  const headingRef = useRef<HTMLHeadingElement>(null);

  // Moving focus to the heading on open is what makes this navigable
  // without a mouse: otherwise focus stays on a table row that is no
  // longer rendered and the next Tab starts from the top of the document.
  useEffect(() => {
    headingRef.current?.focus();
  }, [tg]);

  const d = detail.data;
  const sub = d?.subscription ?? null;
  const active = d?.subscription_is_active ?? isSubscriptionActive(sub);

  if (detail.isError) {
    return (
      <>
        <BackBar onBack={onBack} />
        <ErrorState
          error={new Error(`Не удалось загрузить пользователя ${tg}.`)}
          onRetry={() => detail.refetch()}
        />
      </>
    );
  }

  const username = d?.user.username;

  return (
    <>
      <BackBar onBack={onBack} onRefresh={() => detail.refetch()} />

      {/* Custom rather than PageHeader: the heading takes focus on open
          and carries the view-transition name of the listing row. */}
      <div className="mb-4 px-1">
        <h1
          ref={headingRef}
          tabIndex={-1}
          // Pairs with the row's name in the listing, so the browser
          // animates one into the other instead of cutting.
          style={{ viewTransitionName: `user-row-${tg}` }}
          className="on-shell text-[26px] font-semibold leading-8 outline-none md:text-[30px]"
        >
          {username ? `@${username}` : "Без имени"}
        </h1>
      </div>

      <Bento>
        <Surface className="sm:col-span-6 xl:col-span-12" variant="raised">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="t-mute text-[13px]">Telegram ID</span>
            <span className="flex items-center gap-1">
              <span className="tabular font-mono text-[14px]">{tg}</span>
              <CopyButton value={String(tg)} label="Копировать Telegram ID" />
            </span>
            <div className="flex flex-wrap gap-1.5">
              {sub &&
                (active ? (
                  <span className="badge-success">Подписка активна</span>
                ) : (
                  <span className="badge-muted">Подписка истекла</span>
                ))}
              {!sub && <span className="badge-muted">Без подписки</span>}
              {d?.user.is_reachable === false && <span className="badge-warning">Бот заблокирован</span>}
            </div>
          </div>
        </Surface>

        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="accent"
          label="Баланс"
          value={d ? `${d.balance_rubles} ₽` : "—"}
          hint="Кошелёк пользователя. Покупки с баланса не считаются доходом повторно."
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="raised"
          label="Тариф"
          value={tariffLabel(sub?.subscription_type)}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="raised"
          label={active ? "Истекает" : "Истекла"}
          value={sub?.expires_at ? fmtDate(sub.expires_at) : "—"}
          sub={sub?.expires_at ? fmtRelative(sub.expires_at) : undefined}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="raised"
          label="Кешбэк"
          value={d ? `${d.cashback_effective_percent}%` : "—"}
          sub={d?.cashback_fixed_percent !== null ? "зафиксирован" : undefined}
          hint="Итоговый процент. Складывается из тира, floor и админского фикса — что именно сработало, видно в карточке кешбэка."
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="raised"
          label="Премиум до"
          value={d?.premium?.expires_at ? fmtDate(d.premium.expires_at) : "—"}
          sub={d ? premiumHint(d.premium) : undefined}
          hint="Премиум-ключ (remnawave_premium_uuid) и дата окончания. Bypass-only подписка премиумом не считается."
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-2"
          size="sm"
          variant="raised"
          label="Обход · осталось"
          value={d ? bypassValue(d.bypass) : "—"}
          sub={d ? bypassHint(d.bypass) : undefined}
          hint="Остаток ГБ обхода — live из Remnawave. Лимит 0 в Remnawave означает безлимит."
        />

        {/* Left: what happened to this person. */}
        <div className="flex min-w-0 flex-col gap-[var(--gap)] sm:col-span-6 xl:col-span-8">
          <UserSubscriptionTechCard subscription={sub} />

          <UserPaymentsTable
            rows={payments.data ?? []}
            isLoading={payments.isLoading}
            isFetching={payments.isFetching}
            isError={payments.isError}
            limit={PAYMENTS_LIMIT}
            onRefresh={() => payments.refetch()}
          />

          <UserTimeline
            rows={history.data}
            isLoading={history.isLoading}
            isError={history.isError}
            onRetry={() => history.refetch()}
          />

          <UserAuditLog
            rows={audit.data}
            isLoading={audit.isLoading}
            isError={audit.isError}
            onRetry={() => audit.refetch()}
          />
        </div>

        {/* Right: what can be done about it. */}
        <div className="flex min-w-0 flex-col gap-[var(--gap)] sm:col-span-6 xl:col-span-4">
          <UserActions
            subscription={sub}
            onGrant={(days, tariff) => m.grant.mutate({ days, tariff })}
            onGrantMinutes={(minutes) => m.grantMinutes.mutate({ minutes })}
            onSwitchTariff={(tariff) => m.switchTariff.mutate({ tariff })}
            onRevoke={() => m.revoke.mutate()}
            onReissueAggregator={() => m.reissueAggregator.mutate()}
            onRefreshSubLinks={() => m.refreshSubLinks.mutate()}
            onReissueSubLinks={() => m.reissueSubLinks.mutate()}
            isPending={
              m.grant.isPending ||
              m.grantMinutes.isPending ||
              m.switchTariff.isPending ||
              m.revoke.isPending ||
              m.reissueAggregator.isPending ||
              m.refreshSubLinks.isPending ||
              m.reissueSubLinks.isPending
            }
          />

          <UserBalanceCard
            balanceRubles={d?.balance_rubles ?? 0}
            onChange={(delta, reason) =>
              m.balanceChange.mutate({ delta_rubles: delta, reason })
            }
            isPending={m.balanceChange.isPending}
          />

          <UserProfileStats
            data={extended.data}
            isLoading={extended.isLoading}
            isError={extended.isError}
            onRetry={() => extended.refetch()}
          />

          <DiscountCard
            title="Скидка на подписку"
            existing={d?.discount ?? null}
            onCreate={(percent, hours) =>
              m.discountCreate.mutate({ percent, expires_in_hours: hours })
            }
            onDelete={() => m.discountDelete.mutate()}
            isPending={m.discountCreate.isPending || m.discountDelete.isPending}
          />

          <DiscountCard
            title="Скидка на ГБ (Обход)"
            hint="Действует на пакеты гигабайтов, не на подписку."
            existing={d?.traffic_discount ?? null}
            onCreate={(percent, hours) =>
              m.trafficDiscountCreate.mutate({ percent, expires_in_hours: hours })
            }
            onDelete={() => m.trafficDiscountDelete.mutate()}
            isPending={
              m.trafficDiscountCreate.isPending || m.trafficDiscountDelete.isPending
            }
          />

          <CashbackFixCard
            fixedPercent={d?.cashback_fixed_percent ?? null}
            floorPercent={d?.user.cashback_floor_percent ?? null}
            effectivePercent={d?.cashback_effective_percent ?? 0}
            onSet={(percent) => m.cashbackFixSet.mutate({ percent })}
            onClear={() => m.cashbackFixClear.mutate()}
            isPending={m.cashbackFixSet.isPending || m.cashbackFixClear.isPending}
          />

          <UserTrialCard trial={d?.trial} />

          <UserDangerZone
            telegramId={tg}
            isPending={m.remove.isPending}
            onDelete={() =>
              m.remove.mutate(undefined, {
                // Navigate rather than invalidate: there is nothing left
                // to refetch, and the previous build did this with a
                // hardcoded window.location.assign that broke whenever
                // the base path moved.
                onSuccess: onBack,
              })
            }
          />
        </div>
      </Bento>
    </>
  );
}

/* ── Premium / bypass tiles (fields from main's /users/{tg}) ────────── */

function premiumHint(p: PremiumState | undefined): string | undefined {
  if (!p) return undefined;
  if (!p.has_entity) return "нет ключа";
  if (!p.is_active) return "истёк";
  const days = daysUntil(p.expires_at);
  return days !== null ? `осталось ${days} дн` : "активен";
}

function bypassValue(b: BypassState | undefined): string {
  if (!b || !b.has_entity) return "—";
  // limit_bytes = 0 in Remnawave means unlimited.
  return b.limit_bytes === 0 ? "∞" : fmtBytes(b.remaining_bytes);
}

function bypassHint(b: BypassState | undefined): string | undefined {
  if (!b) return undefined;
  if (!b.has_entity) return "нет ключа";
  if (b.limit_bytes === 0) return "безлимит";
  return `из ${fmtBytes(b.limit_bytes)} · потрачено ${fmtBytes(b.used_bytes)}`;
}

/** The iOS standalone app has no browser back, so "К списку" is the only
    way out of a card — it stays a real, full-size button. */
function BackBar({ onBack, onRefresh }: { onBack: () => void; onRefresh?: () => void }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3 px-1">
      <button type="button" onClick={onBack} className="btn-secondary">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        К списку
      </button>
      {onRefresh && (
        <IconButton label="Обновить данные пользователя" onClick={onRefresh}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        </IconButton>
      )}
    </div>
  );
}
