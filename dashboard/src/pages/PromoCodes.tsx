import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  RefreshCcw,
  Power,
  X,
  Copy,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useIdempotencyKeys } from "@/hooks/useIdempotencyKeys";
import { fmtNum, fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

interface PromoRow extends Record<string, unknown> {
  id?: number;
  code?: string;
  discount_percent?: number;
  uses_count?: number;
  max_uses?: number;
  is_active?: boolean;
  expires_at?: string;
  created_at?: string;
}

export function PromoCodes() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const list = useQuery({
    queryKey: ["promo", "list"],
    queryFn: () => endpoints.promoList() as Promise<PromoRow[]>,
    refetchInterval: 15_000,
  });

  return (
    <div>
      <PageHeader
        title="Промокоды"
        sub="Маркетинг"
        actions={
          <>
            <button
              type="button"
              onClick={() => list.refetch()}
              className="btn-secondary"
            >
              <RefreshCcw className="h-3.5 w-3.5" /> Обновить
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="btn-primary"
            >
              <Plus className="h-3.5 w-3.5" /> Создать
            </button>
          </>
        }
      />

      <Surface label="Все коды" aside={list.isFetching ? <Spinner /> : undefined}>
        {list.isLoading ? (
          <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : list.isError && !list.data ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} className="bg-tile-3" />
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            title="Нет промокодов"
            hint="Создай первый — пользователи смогут применять его при покупке."
            action={
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="btn-secondary mt-2"
              >
                <Plus className="h-3.5 w-3.5" /> Создать
              </button>
            }
          />
        ) : (
          <div className="-mx-2 overflow-x-auto px-2">
            <table className="dtable min-w-[640px]">
              <thead>
                <tr>
                  <th>Код</th>
                  <th>Скидка</th>
                  <th className="num">Использовано</th>
                  <th>Истекает</th>
                  <th>Статус</th>
                  <th aria-label="Действия"></th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((p) => (
                  <PromoRowItem
                    key={Number(p.id ?? 0)}
                    p={p}
                    onChange={() =>
                      qc.invalidateQueries({ queryKey: ["promo"] })
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Surface>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["promo"] });
          }}
        />
      )}
    </div>
  );
}

function PromoRowItem({
  p,
  onChange,
}: {
  p: PromoRow;
  onChange: () => void;
}) {
  const code = String(p.code ?? "");
  const uses = asNum(p.uses_count) ?? 0;
  const max = asNum(p.max_uses) ?? 0;
  const exhausted = max > 0 && uses >= max;
  const expired =
    typeof p.expires_at === "string" &&
    new Date(p.expires_at).getTime() < Date.now();
  const active = p.is_active && !exhausted && !expired;

  const deact = useMutation({
    mutationFn: () => endpoints.promoDeactivate(Number(p.id ?? 0)),
    onSuccess: () => {
      toast.success("Промокод отключён");
      onChange();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  const react = useMutation({
    mutationFn: () => endpoints.promoReactivate(Number(p.id ?? 0)),
    onSuccess: () => {
      toast.success("Промокод включён");
      onChange();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Ошибка"),
  });

  return (
    <tr>
      <td>
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(code);
            toast.info("Скопировано");
          }}
          className="tap-target inline-flex items-center gap-1.5 font-mono font-semibold hover:text-accent"
          aria-label={`Скопировать код ${code}`}
          title="Скопировать"
        >
          {code}
          <Copy className="t-mute h-3 w-3" aria-hidden="true" />
        </button>
      </td>
      <td>
        <span className="badge-accent tabular">-{fmtNum(asNum(p.discount_percent))}%</span>
      </td>
      <td className="num t-body">
        {uses} / {max || "∞"}
      </td>
      <td className="t-body tabular whitespace-nowrap">
        {typeof p.expires_at === "string" ? fmtDate(p.expires_at) : "—"}
      </td>
      <td>
        {active ? (
          <span className="badge-success">
            <CheckCircle2 className="h-3 w-3" /> активен
          </span>
        ) : expired ? (
          <span className="badge-muted">
            <AlertCircle className="h-3 w-3" /> истёк
          </span>
        ) : exhausted ? (
          <span className="badge-muted">
            <AlertCircle className="h-3 w-3" /> исчерпан
          </span>
        ) : (
          <span className="badge-danger">
            <Power className="h-3 w-3" /> отключен
          </span>
        )}
      </td>
      <td className="text-right">
        {active ? (
          <button
            type="button"
            onClick={() => {
              if (confirm(`Отключить ${code}?`)) deact.mutate();
            }}
            disabled={deact.isPending}
            className="btn-ghost text-danger hover:text-danger"
          >
            {deact.isPending ? <Spinner /> : <Power className="h-3.5 w-3.5" />}
            Отключить
          </button>
        ) : !exhausted && !expired ? (
          // Currently disabled and could be re-enabled (still within
          // expiry window and not max-uses-out).
          <button
            type="button"
            onClick={() => {
              if (confirm(`Включить ${code}?`)) react.mutate();
            }}
            disabled={react.isPending}
            className="btn-ghost text-success hover:text-success"
          >
            {react.isPending ? <Spinner /> : <Power className="h-3.5 w-3.5" />}
            Включить
          </button>
        ) : null}
      </td>
    </tr>
  );
}

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [code, setCode] = useState("");
  const [percent, setPercent] = useState(20);
  const [unit, setUnit] = useState<"hours" | "days" | "months">("days");
  const [duration, setDuration] = useState(7);
  const [maxUses, setMaxUses] = useState(100);

  const seconds =
    unit === "hours"
      ? duration * 3600
      : unit === "days"
      ? duration * 86400
      : duration * 30 * 86400;

  const submitKeys = useIdempotencyKeys();
  const create = useMutation({
    mutationFn: () => {
      const body = {
        code: code.trim().toUpperCase(),
        discount_percent: percent,
        duration_seconds: seconds,
        max_uses: maxUses,
      };
      return endpoints.promoCreate(body, submitKeys.opts("promo", body));
    },
    onSuccess: (data) => {
      submitKeys.settle("promo");
      toast.success(`Промокод ${data.code} создан`);
      onCreated();
    },
    onError: (e: unknown) => {
      const err = e as ApiError;
      if (err?.status === 409) {
        toast.error("Такой код уже занят");
      } else {
        toast.error(err?.detail ?? "Ошибка создания");
      }
    },
  });

  const codeValid = /^[A-Z0-9]{3,32}$/.test(code.trim().toUpperCase());

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4 backdrop-blur-sm">
      <div
        className="tile w-full max-w-md p-5 animate-slide-up"
        role="dialog"
        aria-modal="true"
        aria-labelledby="promo-create-title"
      >
        <div className="mb-5 flex items-center justify-between gap-3">
          <h3 id="promo-create-title" className="text-[18px] font-semibold">
            Новый промокод
          </h3>
          <IconButton label="Закрыть" small onClick={onClose} className="bg-tile-3">
            <X className="h-4 w-4" />
          </IconButton>
        </div>

        <div className="flex flex-col gap-4">
          <label className="block">
            <span className="t-mute mb-1.5 block text-[13px]">Код (A-Z 0-9)</span>
            <input
              className="input font-mono uppercase"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              maxLength={32}
              placeholder="SUMMER25"
              autoFocus
            />
            {code && !codeValid && (
              <span className="mt-1.5 block text-[12px] text-danger">
                Только A-Z и 0-9, 3-32 символа
              </span>
            )}
          </label>

          <label className="block">
            <span className="t-mute mb-1.5 block text-[13px]">Скидка %</span>
            <input
              className="input"
              type="number"
              min={1}
              max={100}
              value={percent}
              onChange={(e) =>
                setPercent(Math.max(1, Math.min(100, Number(e.target.value) || 1)))
              }
            />
          </label>

          <div className="grid grid-cols-3 gap-2">
            <label className="col-span-2 block">
              <span className="t-mute mb-1.5 block text-[13px]">Длительность</span>
              <input
                className="input"
                type="number"
                min={1}
                value={duration}
                onChange={(e) =>
                  setDuration(Math.max(1, Number(e.target.value) || 1))
                }
              />
            </label>
            <label className="block">
              <span className="t-mute mb-1.5 block text-[13px]">Единица</span>
              <select
                className="input"
                value={unit}
                onChange={(e) => setUnit(e.target.value as typeof unit)}
              >
                <option value="hours">часов</option>
                <option value="days">дней</option>
                <option value="months">месяцев</option>
              </select>
            </label>
          </div>

          <label className="block">
            <span className="t-mute mb-1.5 block text-[13px]">Максимум применений</span>
            <input
              className="input"
              type="number"
              min={1}
              max={1000000}
              value={maxUses}
              onChange={(e) =>
                setMaxUses(Math.max(1, Number(e.target.value) || 1))
              }
            />
          </label>
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
            disabled={create.isPending || !codeValid}
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
