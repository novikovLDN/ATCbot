import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Tag,
  Plus,
  RefreshCcw,
  Power,
  X,
  Copy,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtNum, fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

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
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Маркетинг
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Промокоды
          </h1>
        </div>
        <div className="flex items-center gap-2">
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
        </div>
      </header>

      <div className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Все коды
          </div>
          {list.isFetching && <Spinner />}
        </div>

        {list.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            icon={Tag}
            title="Нет промокодов"
            description="Создай первый — пользователи смогут применять его при покупке."
            action={
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="btn-primary"
              >
                <Plus className="h-3.5 w-3.5" /> Создать
              </button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                  <th className="px-2 py-2 font-medium">Код</th>
                  <th className="px-2 py-2 font-medium">Скидка</th>
                  <th className="px-2 py-2 font-medium">Использовано</th>
                  <th className="px-2 py-2 font-medium">Истекает</th>
                  <th className="px-2 py-2 font-medium">Статус</th>
                  <th className="px-2 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
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
      </div>

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
    <tr className="hover:bg-accent/[0.04]">
      <td className="px-2 py-2">
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(code);
            toast.info("Скопировано");
          }}
          className="inline-flex items-center gap-1.5 font-mono font-semibold text-fg hover:text-accent"
        >
          {code}
          <Copy className="h-3 w-3 text-fg-subtle" />
        </button>
      </td>
      <td className="px-2 py-2 text-fg">
        <span className="badge-accent">-{fmtNum(asNum(p.discount_percent))}%</span>
      </td>
      <td className="px-2 py-2 text-fg-muted">
        {uses} / {max || "∞"}
      </td>
      <td className="px-2 py-2 text-fg-muted">
        {typeof p.expires_at === "string" ? fmtDate(p.expires_at) : "—"}
      </td>
      <td className="px-2 py-2">
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
      <td className="px-2 py-2 text-right">
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

  const create = useMutation({
    mutationFn: () =>
      endpoints.promoCreate({
        code: code.trim().toUpperCase(),
        discount_percent: percent,
        duration_seconds: seconds,
        max_uses: maxUses,
      }),
    onSuccess: (data) => {
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
      <div className="card w-full max-w-md p-6 animate-slide-up">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-wider text-fg-subtle">
              Новый промокод
            </div>
            <h3 className="mt-1 text-lg font-semibold text-fg">Параметры</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost"
            aria-label="Закрыть"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="block">
            <div className="mb-1 text-xs text-fg-subtle">Код (A-Z 0-9)</div>
            <input
              className="input font-mono uppercase"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              maxLength={32}
              placeholder="SUMMER25"
              autoFocus
            />
            {code && !codeValid && (
              <div className="mt-1 text-xs text-danger">
                Только A-Z и 0-9, 3-32 символа
              </div>
            )}
          </label>

          <label className="block">
            <div className="mb-1 text-xs text-fg-subtle">Скидка %</div>
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
            <label className="block col-span-2">
              <div className="mb-1 text-xs text-fg-subtle">Длительность</div>
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
              <div className="mb-1 text-xs text-fg-subtle">Единица</div>
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
            <div className="mb-1 text-xs text-fg-subtle">Максимум применений</div>
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

        <div className="mt-6 flex items-center justify-between">
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
