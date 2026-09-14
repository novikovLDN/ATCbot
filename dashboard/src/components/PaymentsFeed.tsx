/**
 * The latest purchases in every state (paid, waiting, expired) — the
 * live feed that used to live on the legacy payments screen.
 * Source: GET /payments/recent (pending_purchases, newest first).
 */
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { PRODUCT_LABEL, PROVIDER_LABEL } from "@/lib/metricsApi";
import { fmtRelative, fmtRub } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { ListRow, StatusDot, type Tone } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

const STATUS: Record<string, { tone: Tone; label: string }> = {
  paid: { tone: "ok", label: "оплачено" },
  pending: { tone: "warn", label: "ждёт оплаты" },
  expired: { tone: "idle", label: "истекло" },
};

function productOf(r: Record<string, unknown>): string {
  const type = String(r.purchase_type ?? "subscription");
  const tariff = String(r.tariff ?? "");
  if (type === "subscription") {
    // Legacy biz_* rows are Plus (backend rule: app/services/tariffs.normalize_tier).
    const base = tariff.startsWith("biz_") ? "plus" : tariff || "basic";
    const key = r.is_combo ? `combo_${base}` : base;
    return PRODUCT_LABEL[key] ?? tariff;
  }
  return PRODUCT_LABEL[type === "traffic_pack" ? "traffic_pack" : `shop_${type}`] ?? PRODUCT_LABEL[type] ?? type;
}

export function PaymentsFeed({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: ["payments-feed"],
    queryFn: () => endpoints.paymentsRecent({ limit: 20 }),
    refetchInterval: 30_000,
  });
  return (
    <Surface className={className} label="Последние покупки">
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !q.data ? (
        <Skeleton className="h-40 w-full" />
      ) : q.data.length === 0 ? (
        <EmptyState title="Покупок пока нет" />
      ) : (
        <ul className="flex flex-col gap-2">
          {q.data.map((r) => {
            const st = STATUS[String(r.status)] ?? { tone: "idle" as Tone, label: String(r.status) };
            const who = r.username ? `@${String(r.username)}` : String(r.telegram_id ?? "");
            const provider = PROVIDER_LABEL[String(r.payment_provider)] ?? String(r.payment_provider ?? "");
            return (
              <li key={String(r.purchase_id ?? r.id)}>
                <ListRow
                  leading={<StatusDot tone={st.tone} label={st.label} />}
                  title={`${productOf(r)}, ${who}`}
                  meta={[st.label, provider, r.created_at ? fmtRelative(String(r.created_at)) : null].filter(Boolean).join(", ")}
                  value={fmtRub(Number(r.price_rubles ?? 0))}
                  to={r.telegram_id ? `/users?tg=${String(r.telegram_id)}` : undefined}
                />
              </li>
            );
          })}
        </ul>
      )}
    </Surface>
  );
}
