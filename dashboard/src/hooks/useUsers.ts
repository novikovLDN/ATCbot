/**
 * Query layer for the Users screen.
 *
 * The screen makes five different requests about the same person, and
 * every mutation invalidates a different subset of them. That bookkeeping
 * used to live inline in the page, where it was wrong: `invalidate()`
 * refreshed only the detail query, so after granting a subscription the
 * search list still said "no sub" and the spend total still showed the
 * pre-top-up figure. One key factory and one invalidator fix that class
 * of bug by construction.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { endpoints, ApiError } from "@/lib/api";
import type { UserListFilters } from "@/types/user";

export const userKeys = {
  all: ["users"] as const,
  list: (f: UserListFilters) => ["users", "list", f] as const,
  search: (q: string) => ["users", "search", q] as const,
  detail: (tg: number) => ["users", "detail", tg] as const,
  extended: (tg: number) => ["users", "extended", tg] as const,
  payments: (tg: number) => ["users", "payments", tg] as const,
  history: (tg: number) => ["users", "history", tg] as const,
  audit: (tg: number) => ["users", "audit", tg] as const,
};

/**
 * Refreshes everything that could have been affected by acting on a user.
 *
 * Deliberately broad. Granting days changes the detail, the listing badge,
 * the history and the audit trail; a balance change alters the spend
 * totals in extended-stats. Working out the minimal set per mutation is
 * how the screen drifted out of sync in the first place, and these are
 * cheap queries against one row.
 */
export function useInvalidateUser() {
  const qc = useQueryClient();
  return useCallback(
    (tg: number) => {
      qc.invalidateQueries({ queryKey: userKeys.detail(tg) });
      qc.invalidateQueries({ queryKey: userKeys.extended(tg) });
      qc.invalidateQueries({ queryKey: userKeys.payments(tg) });
      qc.invalidateQueries({ queryKey: userKeys.history(tg) });
      qc.invalidateQueries({ queryKey: userKeys.audit(tg) });
      // The listing and any open search both carry has_active_sub and
      // expires_at for this person.
      qc.invalidateQueries({ queryKey: ["users", "list"] });
      qc.invalidateQueries({ queryKey: ["users", "search"] });
    },
    [qc],
  );
}

/** A 404 means the user does not exist; retrying it three times only
    delays showing that. */
function retryUnlessMissing(failureCount: number, err: unknown) {
  if (err instanceof ApiError && (err.status === 404 || err.status === 401)) {
    return false;
  }
  return failureCount < 2;
}

export function useUsersList(filters: UserListFilters, enabled = true) {
  return useQuery({
    queryKey: userKeys.list(filters),
    queryFn: () => endpoints.usersList(filters),
    enabled,
    // Keep the current page on screen while the next one loads. A
    // skeleton flash on every sort or page change reads as breakage and
    // loses the reader's place in a long table.
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });
}

export function useUserSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: userKeys.search(q),
    queryFn: () => endpoints.userSearch(q),
    // Guards against a whitespace-only `?tg=` deep link firing a request.
    enabled: q.length > 0,
    retry: false,
    staleTime: 30_000,
  });
}

export function useUserDetail(tg: number | null) {
  return useQuery({
    queryKey: userKeys.detail(tg ?? 0),
    queryFn: () => endpoints.userDetail(tg as number),
    enabled: tg !== null,
    retry: retryUnlessMissing,
    staleTime: 15_000,
  });
}

export function useUserExtended(tg: number | null) {
  return useQuery({
    queryKey: userKeys.extended(tg ?? 0),
    queryFn: () => endpoints.userExtended(tg as number),
    enabled: tg !== null,
    retry: retryUnlessMissing,
    staleTime: 30_000,
  });
}

export function useUserPayments(tg: number | null, limit = 100) {
  return useQuery({
    queryKey: userKeys.payments(tg ?? 0),
    queryFn: () => endpoints.userPayments(tg as number, limit),
    enabled: tg !== null,
    retry: retryUnlessMissing,
    staleTime: 30_000,
  });
}

export function useUserHistory(tg: number | null, limit = 50) {
  return useQuery({
    queryKey: userKeys.history(tg ?? 0),
    queryFn: () => endpoints.userHistory(tg as number, limit),
    enabled: tg !== null,
    retry: retryUnlessMissing,
    staleTime: 30_000,
  });
}

export function useUserAudit(tg: number | null, limit = 50) {
  return useQuery({
    queryKey: userKeys.audit(tg ?? 0),
    queryFn: () => endpoints.auditRecent(limit, tg as number),
    enabled: tg !== null,
    retry: retryUnlessMissing,
    staleTime: 30_000,
  });
}
