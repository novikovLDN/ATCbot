import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Gift,
  Plus,
  Trash2,
  Copy,
  ChevronRight,
  RefreshCcw,
  TrendingUp,
  Database,
  Users as UsersIcon,
  X,
  Calendar,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useIdempotencyKeys } from "@/hooks/useIdempotencyKeys";
import { useBranding } from "@/lib/branding";
import { fmtNum, fmtDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { IconButton, ListRow, Segmented } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

function RowsSkeleton() {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
      <Skeleton className="h-[52px] w-full rounded-row" />
      <Skeleton className="h-[52px] w-full rounded-row" />
      <Skeleton className="h-[52px] w-2/3 rounded-row" />
    </div>
  );
}

export function BypassGifts() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  const summary = useQuery({
    queryKey: ["bgift", "summary"],
    queryFn: endpoints.bgiftSummary,
  });
  const list = useQuery({
    queryKey: ["bgift", "list"],
    queryFn: () => endpoints.bgiftList(0, 50, false),
    refetchInterval: 30_000,
  });

  return (
    <>
      <PageHeader
        title="Гифт-ссылки на ГБ"
        sub="Промо: ссылка на бота, по которой пользователь получает N ГБ обхода."
        actions={
          <>
            <button type="button" onClick={() => list.refetch()} className="btn-secondary">
              <RefreshCcw className="h-3.5 w-3.5" /> Обновить
            </button>
            <button type="button" onClick={() => setShowCreate(true)} className="btn-primary">
              <Plus className="h-3.5 w-3.5" /> Создать
            </button>
          </>
        }
      />

      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          label="Всего ссылок"
          value={fmtNum(asNum(summary.data?.total_links))}
          loading={summary.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="raised"
          label="Активных"
          value={fmtNum(asNum(summary.data?.active_links))}
          loading={summary.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="raised"
          label="Использовано"
          value={fmtNum(asNum(summary.data?.total_redemptions))}
          loading={summary.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="accent"
          label="Выдано ГБ"
          value={`${fmtNum(asNum(summary.data?.total_gb_granted))} GB`}
          loading={summary.isLoading}
        />

        <Surface className="sm:col-span-6 xl:col-span-7" label="Активные ссылки">
          {list.isLoading ? (
            <RowsSkeleton />
          ) : list.isError && !list.data ? (
            <ErrorState className="bg-tile-3" error={list.error} onRetry={() => list.refetch()} />
          ) : !list.data || list.data.length === 0 ? (
            <EmptyState
              title="Ссылок ещё нет"
              hint="Создай первую — поделишься ею с пользователями, при переходе бот выдаст N ГБ обхода."
              action={
                <button type="button" onClick={() => setShowCreate(true)} className="btn-primary mt-2">
                  <Plus className="h-3.5 w-3.5" /> Создать
                </button>
              }
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {list.data.map((b) => {
                const id = Number(b.id ?? 0);
                if (!id) return null;
                const uses = asNum(b.redemption_count) ?? 0;
                const max = asNum(b.max_uses) ?? 0;
                const isSel = selected === id;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelected(id)}
                      aria-pressed={isSel}
                      className={cn("list-row w-full text-left", isSel && "bg-tile-4")}
                    >
                      <Gift className="t-mute h-4 w-4 flex-none" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="truncate font-mono text-[14px] font-medium">
                            {String(b.code ?? "—")}
                          </span>
                          <span className="badge-accent tabular">{fmtNum(asNum(b.gb_amount))} GB</span>
                          <span className={cn("tabular", uses >= max && max > 0 ? "badge-danger" : "badge-muted")}>
                            {uses}/{max} исп.
                          </span>
                        </span>
                        <span className="t-mute mt-0.5 block text-[12px]">
                          действует до {fmtDate(String(b.expires_at ?? ""))}
                        </span>
                      </span>
                      <span className="icon-btn icon-btn-sm bg-tile-1" aria-hidden="true">
                        <ChevronRight className="h-3.5 w-3.5" />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Surface>

        {selected ? (
          <GiftDetail
            id={selected}
            onDeleted={() => {
              setSelected(null);
              qc.invalidateQueries({ queryKey: ["bgift"] });
            }}
          />
        ) : (
          <Surface className="hidden xl:col-span-5 xl:block" variant="raised" label="Детали">
            <EmptyState
              title="Выбери ссылку"
              hint="Кликни — увидишь детали и список тех, кто активировал."
            />
          </Surface>
        )}
      </Bento>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["bgift"] });
          }}
        />
      )}
    </>
  );
}

function GiftDetail({
  id,
  onDeleted,
}: {
  id: number;
  onDeleted: () => void;
}) {
  const brand = useBranding();
  const detail = useQuery({
    queryKey: ["bgift", "detail", id],
    queryFn: () => endpoints.bgiftDetail(id),
  });
  const redemptions = useQuery({
    queryKey: ["bgift", "redemptions", id],
    queryFn: () => endpoints.bgiftRedemptions(id, 200),
  });
  const del = useMutation({
    mutationFn: () => endpoints.bgiftDelete(id),
    onSuccess: () => {
      toast.success("Ссылка удалена");
      onDeleted();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось удалить"),
  });

  const col = "flex flex-col gap-[var(--gap)] sm:col-span-6 xl:col-span-5";

  if (detail.isLoading) {
    return (
      <div className={col}>
        <Surface variant="raised" label="Код">
          <Skeleton className="h-7 w-40" />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Skeleton className="h-16 w-full rounded-row" />
            <Skeleton className="h-16 w-full rounded-row" />
          </div>
        </Surface>
      </div>
    );
  }
  if (!detail.data) {
    return (
      <div className={col}>
        <Surface variant="raised" label="Детали">
          <EmptyState title="Не найдено" hint="Возможно ссылку удалили." />
        </Surface>
      </div>
    );
  }

  const b = detail.data;
  const code = String(b.code ?? "");
  // Format from app/handlers/start.py — deep link to the bot.
  // The bot name comes from the backend (config.BOT_USERNAME); nothing is
  // hardcoded here. Until branding loads the link is simply not offered.
  const shareUrl = brand.bot_username ? `https://t.me/${brand.bot_username}?start=bgift_${code}` : "";

  return (
    <div className={cn(col, "animate-fade-in")}>
      <Surface
        variant="raised"
        label="Код"
        aside={
          <button
            type="button"
            onClick={() => {
              if (confirm("Удалить ссылку? Уже активированные сохранятся."))
                del.mutate();
            }}
            disabled={del.isPending}
            className="btn-danger"
          >
            <Trash2 className="h-3.5 w-3.5" /> Удалить
          </button>
        }
      >
        <div className="truncate font-mono text-[22px] font-semibold leading-7">{code}</div>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <Cell icon={Database} label="ГБ" value={`${fmtNum(asNum(b.gb_amount))} GB`} />
          <Cell icon={Calendar} label="Срок" value={`${fmtNum(asNum(b.validity_days))} дн`} />
          <Cell icon={UsersIcon} label="Максимум" value={fmtNum(asNum(b.max_uses))} />
          <Cell icon={TrendingUp} label="Истекает" value={fmtDate(String(b.expires_at ?? ""))} />
        </div>

        <div className="mt-2 rounded-row bg-tile-3 p-3">
          <div className="t-mute mb-1 text-[12px]">Поделиться</div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate font-mono text-[12px]">
              {shareUrl || "Ссылка появится, когда загрузится имя бота"}
            </code>
            <IconButton
              label="Скопировать"
              small
              disabled={!shareUrl}
              onClick={() => {
                navigator.clipboard.writeText(shareUrl);
                toast.success("Скопировано");
              }}
            >
              <Copy className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        </div>
      </Surface>

      <Surface
        label="Активации"
        aside={<span className="capsule-label tabular">{fmtNum(redemptions.data?.total ?? 0)}</span>}
      >
        {redemptions.isLoading ? (
          <RowsSkeleton />
        ) : !redemptions.data || redemptions.data.rows.length === 0 ? (
          <p className="t-mute text-[13px]">Никто не активировал.</p>
        ) : (
          <ul className="flex max-h-[400px] flex-col gap-2 overflow-y-auto">
            {redemptions.data.rows.map((r, i) => (
              <li key={i}>
                <ListRow
                  title={`tg:${String(r.telegram_id ?? "—")}`}
                  meta={fmtDate(String(r.redeemed_at ?? ""))}
                  trailing={
                    <span className="badge-success tabular flex-none">
                      +{fmtNum(asNum(r.gb_granted))} GB
                    </span>
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </Surface>
    </div>
  );
}

function Cell({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Gift;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-row bg-tile-3 p-3">
      <div className="t-mute flex items-center gap-1.5 text-[12px]">
        <Icon className="h-3 w-3" aria-hidden="true" /> {label}
      </div>
      <div className="tabular mt-1 truncate text-[14px] font-semibold">{value}</div>
    </div>
  );
}

const GB_PRESETS = [1, 3, 5, 10, 20, 50].map((v) => ({ value: v, label: String(v) }));
const DAY_PRESETS = [1, 3, 5, 7, 14, 30].map((v) => ({ value: v, label: String(v) }));
const USE_PRESETS = [1, 5, 10, 50, 100, 500].map((v) => ({ value: v, label: String(v) }));

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [gb, setGb] = useState(5);
  const [days, setDays] = useState(7);
  const [maxUses, setMaxUses] = useState(10);

  const submitKeys = useIdempotencyKeys();
  const create = useMutation({
    mutationFn: () => {
      const body = {
        gb_amount: gb,
        validity_days: days,
        max_uses: maxUses,
      };
      return endpoints.bgiftCreate(body, submitKeys.opts("bgift", body));
    },
    onSuccess: () => {
      submitKeys.settle("bgift");
      toast.success("Ссылка создана");
      onCreated();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось создать"),
  });

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4 backdrop-blur-sm">
      <div className="tile w-full max-w-md p-5 animate-slide-up" role="dialog" aria-modal="true" aria-labelledby="bgift-create-title">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="t-mute text-[13px]">Новая ссылка</p>
            <h3 id="bgift-create-title" className="text-[18px] font-semibold">Гифт-ГБ</h3>
          </div>
          <IconButton label="Закрыть" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <label className="block">
              <span className="t-mute mb-1.5 block text-[13px]">ГБ обхода</span>
              <input
                className="input tabular"
                type="number"
                min={1}
                max={1024}
                value={gb}
                onChange={(e) => setGb(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <div className="mt-2 overflow-x-auto">
              <Segmented label="Быстрый выбор: ГБ" value={gb} options={GB_PRESETS} onChange={setGb} />
            </div>
          </div>

          <div>
            <label className="block">
              <span className="t-mute mb-1.5 block text-[13px]">Срок действия (дней)</span>
              <input
                className="input tabular"
                type="number"
                min={1}
                max={365}
                value={days}
                onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <div className="mt-2 overflow-x-auto">
              <Segmented label="Быстрый выбор: дней" value={days} options={DAY_PRESETS} onChange={setDays} />
            </div>
          </div>

          <div>
            <label className="block">
              <span className="t-mute mb-1.5 block text-[13px]">Максимум активаций</span>
              <input
                className="input tabular"
                type="number"
                min={1}
                max={10000}
                value={maxUses}
                onChange={(e) =>
                  setMaxUses(Math.max(1, Number(e.target.value) || 1))
                }
              />
            </label>
            <div className="mt-2 overflow-x-auto">
              <Segmented label="Быстрый выбор: активаций" value={maxUses} options={USE_PRESETS} onChange={setMaxUses} />
            </div>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost"
            disabled={create.isPending}
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="btn-primary"
          >
            {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
            Создать
          </button>
        </div>
      </div>
    </div>
  );
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
