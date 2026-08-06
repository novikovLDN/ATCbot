import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, Input, Modal } from "@/components/ui";

/**
 * Создание промокода.
 *
 * ПРЕДПРОСМОТР ВМЕСТО ЧЕТЫРЁХ ПОЛЕЙ. Форма считает и показывает то, что
 * получится: «SUMMER25 даёт −20 % от цены, до 100 применений, работает до
 * 12.09.2026». Из четырёх отдельных чисел («20», «7», «дней», «100») это
 * не собирается в голове, а ошибка в единице измерения — самая частая:
 * «7» с выбранными часами вместо дней даёт код, живущий до вечера.
 *
 * СРОК СЧИТАЕТСЯ ОТ МОМЕНТА СОЗДАНИЯ. Сервер принимает длительность в
 * секундах и сам ставит expires_at = сейчас + длительность, поэтому дата
 * в предпросмотре — оценка на момент нажатия, и это честно написано.
 */

const UNITS: Array<{ key: "hours" | "days" | "months"; label: string; seconds: number }> = [
  { key: "hours", label: "часов", seconds: 3600 },
  { key: "days", label: "дней", seconds: 86_400 },
  { key: "months", label: "месяцев", seconds: 30 * 86_400 },
];

export function PromoCreate({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();

  const [code, setCode] = useState("");
  const [percent, setPercent] = useState("20");
  const [duration, setDuration] = useState("7");
  const [unit, setUnit] = useState<"hours" | "days" | "months">("days");
  const [maxUses, setMaxUses] = useState("100");

  const normalized = code.trim().toUpperCase();
  const codeValid = /^[A-Z0-9]{3,32}$/.test(normalized);
  const codeError =
    normalized === "" || codeValid ? undefined : "Только латиница A–Z и цифры, от 3 до 32 знаков";

  const percentNum = Number(percent.trim());
  const percentValid = Number.isInteger(percentNum) && percentNum >= 1 && percentNum <= 100;
  const percentError = percent.trim() === "" || percentValid ? undefined : "Целое от 1 до 100";

  const durationNum = Number(duration.trim());
  const durationValid = Number.isInteger(durationNum) && durationNum >= 1;
  const durationError = duration.trim() === "" || durationValid ? undefined : "Целое число, от 1";

  const usesNum = Number(maxUses.trim());
  const usesValid = Number.isInteger(usesNum) && usesNum >= 1 && usesNum <= 1_000_000;
  const usesError = maxUses.trim() === "" || usesValid ? undefined : "Целое от 1 до 1 000 000";

  const seconds = durationValid
    ? durationNum * (UNITS.find((u) => u.key === unit)?.seconds ?? 86_400)
    : 0;
  const expiresIso = seconds > 0 ? new Date(Date.now() + seconds * 1000).toISOString() : null;

  const ready = codeValid && percentValid && durationValid && usesValid;

  const create = useMutation({
    mutationFn: () =>
      endpoints.promoCreate({
        code: normalized,
        discount_percent: percentNum,
        duration_seconds: seconds,
        max_uses: usesNum,
      }),
    onSuccess: (data) => {
      toast.success(`Промокод ${data.code} создан`);
      qc.invalidateQueries({ queryKey: ["promo"] });
      setCode("");
      onClose();
    },
    onError: (e: unknown) => {
      const err = e as ApiError;
      // 409 — единственный ожидаемый отказ, и он про конкретную причину.
      // Общее «ошибка создания» на нём заставило бы гадать.
      toast.error(
        err?.status === 409
          ? `Код ${normalized} уже занят — придумайте другой`
          : (err?.detail ?? "Не удалось создать промокод"),
      );
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Новый промокод"
      description="Код применяется при покупке и снижает цену на указанный процент."
      size="md"
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            disabled={!ready}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            Создать код
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Input
          label="Код"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          maxLength={32}
          placeholder="SUMMER25"
          mono
          autoComplete="off"
          spellCheck={false}
          error={codeError}
          hint={codeError ? undefined : "Его будут набирать вручную — короткий и без похожих знаков"}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Скидка"
            value={percent}
            onChange={(e) => setPercent(e.target.value)}
            inputMode="numeric"
            trailing="%"
            error={percentError}
          />
          <Input
            label="Максимум применений"
            value={maxUses}
            onChange={(e) => setMaxUses(e.target.value)}
            inputMode="numeric"
            error={usesError}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <Input
            label="Сколько живёт код"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            inputMode="numeric"
            error={durationError}
          />
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted" htmlFor="promo-unit">
              Единица
            </label>
            <select
              id="promo-unit"
              value={unit}
              onChange={(e) => setUnit(e.target.value as typeof unit)}
              className="h-9 rounded-md border border-border-control bg-bg-card px-2.5 text-base text-fg"
            >
              {UNITS.map((u) => (
                <option key={u.key} value={u.key}>
                  {u.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Предпросмотр. Он и есть защита от ошибки в единице измерения. */}
        <div className="rounded-md border border-border bg-bg-subtle p-3 text-base text-fg-muted">
          {ready ? (
            <>
              Код <span className="font-mono font-medium text-fg">{normalized}</span> снизит цену
              на <span className="font-medium text-fg">{percentNum}%</span>, сработает не больше{" "}
              <span className="font-medium text-fg">{usesNum}</span>{" "}
              {usesNum === 1 ? "раза" : "раз"} и перестанет действовать{" "}
              <span className="font-medium text-fg">{fmtDate(expiresIso)}</span>.
              <div className="mt-1 text-xs text-fg-subtle">
                Отсчёт срока начнётся в момент создания, поэтому дата сдвинется на
                то время, что форма открыта.
              </div>
            </>
          ) : (
            "Заполните поля — здесь появится, что именно получит покупатель и до какого числа."
          )}
        </div>
      </div>
    </Modal>
  );
}
