/**
 * Every write action available on a user, in one place.
 *
 * Two things this fixes structurally:
 *
 * 1. Each mutation takes its payload as a `mutate()` argument rather than
 *    reading component state through a closure. The balance control used
 *    to do the latter, via `setDelta(-x)` followed by
 *    `setTimeout(() => mutate(), 0)` — the mutation read whatever `delta`
 *    the render it closed over had. It happened to work because React 18
 *    commits state before the macrotask runs, but StrictMode, Suspense or
 *    concurrent rendering would each have turned a withdrawal into a
 *    deposit. On a live balance that is real money moving the wrong way.
 *
 * 2. Errors surface. Silent `onError` handlers meant a rejected grant
 *    looked identical to a successful one until the page was reloaded.
 */
import { useMutation } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { useIdempotencyKeys } from "./useIdempotencyKeys";
import { toast } from "@/store/toast";
import { useInvalidateUser } from "./useUsers";
import { ApiError } from "@/lib/api";

function describe(err: unknown): string {
  if (err instanceof ApiError) return err.detail || `Ошибка ${err.status}`;
  if (err instanceof Error) return err.message;
  return "Неизвестная ошибка";
}

export function useUserMutations(tg: number) {
  const invalidate = useInvalidateUser();
  const keys = useIdempotencyKeys();

  /** Shared success/failure handling so no call site can forget it. */
  function opts<TVars, TData>(action: string, successText: string | ((d: TData) => string)) {
    return {
      onSuccess: (data: TData) => {
        keys.settle(action);
        toast.success(
          typeof successText === "function" ? successText(data) : successText,
        );
        invalidate(tg);
      },
      onError: (err: unknown) => {
        keys.settle(action, err);
        toast.error(describe(err));
      },
    } as const;
  }

  return {
    grant: useMutation({
      mutationFn: (v: { days: number; tariff: string }) =>
        endpoints.userGrant(tg, v, keys.opts("grant", v)),
      ...opts("grant", "Доступ выдан"),
    }),

    grantMinutes: useMutation({
      mutationFn: (v: { minutes: number }) =>
        endpoints.userGrantMinutes(tg, v, keys.opts("grantMinutes", v)),
      ...opts("grantMinutes", "Доступ выдан на минуты"),
    }),

    switchTariff: useMutation({
      mutationFn: (v: { tariff: string }) =>
        endpoints.userSwitchTariff(tg, v, keys.opts("switchTariff", v)),
      ...opts("switchTariff", "Тариф изменён"),
    }),

    revoke: useMutation({
      mutationFn: () => endpoints.userRevoke(tg, keys.opts("revoke")),
      ...opts("revoke", "Доступ отозван"),
    }),

    /**
     * New aggregator token: the old link stops working at once. Touches only
     * this user — upstream links and other users are unaffected. The new
     * URL is copied to the clipboard as a convenience.
     */
    reissueAggregator: useMutation({
      mutationFn: () => endpoints.userReissueAggregator(tg, keys.opts("reissue")),
      onSuccess: (r: { ok: boolean; url: string }) => {
        keys.settle("reissue");
        toast.success("Ссылка перевыпущена — старая больше не работает");
        try {
          if (r?.url) void navigator.clipboard?.writeText(r.url);
        } catch {
          /* clipboard unavailable — not critical */
        }
        invalidate(tg);
      },
      onError: (err: unknown) => {
        keys.settle("reissue", err);
        toast.error(describe(err));
      },
    }),

    /**
     * `delta_rubles` is signed and comes from the caller. Withdrawal is
     * `mutate({ delta_rubles: -n })` — there is no separate "subtract"
     * path that mutates state first and hopes the read wins the race.
     */
    balanceChange: useMutation({
      mutationFn: (v: { delta_rubles: number; reason?: string }) =>
        endpoints.userBalanceChange(tg, v, keys.opts("balance", v)),
      onSuccess: (data: { new_balance_rubles: number }) => {
        keys.settle("balance");
        toast.success(`Баланс: ${data.new_balance_rubles} ₽`);
        invalidate(tg);
      },
      onError: (err: unknown) => {
        keys.settle("balance", err);
        toast.error(describe(err));
      },
    }),

    discountCreate: useMutation({
      mutationFn: (v: { percent: number; expires_in_hours: number | null }) =>
        endpoints.userDiscountCreate(tg, v, keys.opts("discount", v)),
      ...opts("discount", "Скидка выдана"),
    }),
    discountDelete: useMutation({
      mutationFn: () => endpoints.userDiscountDelete(tg, keys.opts("discountDelete")),
      ...opts("discountDelete", "Скидка снята"),
    }),

    trafficDiscountCreate: useMutation({
      mutationFn: (v: { percent: number; expires_in_hours: number | null }) =>
        endpoints.userTrafficDiscountCreate(tg, v, keys.opts("trafficDiscount", v)),
      ...opts("trafficDiscount", "Скидка на ГБ выдана"),
    }),
    trafficDiscountDelete: useMutation({
      mutationFn: () => endpoints.userTrafficDiscountDelete(tg, keys.opts("trafficDiscountDelete")),
      ...opts("trafficDiscountDelete", "Скидка на ГБ снята"),
    }),

    cashbackFixSet: useMutation({
      mutationFn: (v: { percent: number }) =>
        endpoints.userCashbackFixSet(tg, v, keys.opts("cashbackFix", v)),
      onSuccess: (data: { effective_percent: number; notify_sent: boolean }) => {
        keys.settle("cashbackFix");
        toast.success(
          `Кешбэк зафиксирован: ${data.effective_percent}%` +
            // A failed notification almost always means the user blocked
            // the bot, which is worth saying rather than leaving the
            // operator to wonder whether the fix applied.
            (data.notify_sent ? "" : " · уведомление не доставлено"),
        );
        invalidate(tg);
      },
      onError: (err: unknown) => {
        keys.settle("cashbackFix", err);
        toast.error(describe(err));
      },
    }),
    /**
     * Cascading delete. Invalidation is pointless afterwards — the row is
     * gone — so the caller navigates instead, and the listing query is
     * dropped rather than refetched for a user that no longer exists.
     */
    remove: useMutation({
      mutationFn: () => endpoints.userDelete(tg, keys.opts("delete")),
      onSuccess: () => {
        keys.settle("delete");
        toast.success("Пользователь удалён");
      },
      onError: (err: unknown) => {
        keys.settle("delete", err);
        toast.error(describe(err));
      },
    }),

    cashbackFixClear: useMutation({
      mutationFn: () => endpoints.userCashbackFixClear(tg, keys.opts("cashbackFixClear")),
      ...opts("cashbackFixClear", "Фикс кешбэка снят"),
    }),
  };
}
