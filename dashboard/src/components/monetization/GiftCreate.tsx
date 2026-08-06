import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";
import { Button, Input, Modal } from "@/components/ui";

/**
 * Создание подарочной ссылки на ГБ обхода.
 *
 * ПРЕДПРОСМОТР СЧИТАЕТ ОБЩИЙ РАСХОД. Три поля — «5 ГБ», «7 дней»,
 * «100 активаций» — по отдельности выглядят безобидно, а вместе означают
 * 500 ГБ трафика, за который уже заплачено. Это и написано под формой:
 * величина, из которой следует решение, а не три числа, из которых её
 * надо собирать в уме.
 *
 * БЫСТРЫЕ ЗНАЧЕНИЯ — ПОДСКАЗКА, А НЕ ОГРАНИЧЕНИЕ. Поле остаётся обычным
 * числовым: сервер принимает до 1024 ГБ, 365 дней и 10 000 активаций.
 */

const GB_PRESETS = [1, 3, 5, 10, 20, 50];
const DAYS_PRESETS = [1, 3, 7, 14, 30, 90];
const USES_PRESETS = [1, 5, 10, 50, 100, 500];

export function GiftCreate({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();

  const [gb, setGb] = useState("5");
  const [days, setDays] = useState("7");
  const [uses, setUses] = useState("10");

  const gbNum = Number(gb.trim());
  const gbValid = Number.isInteger(gbNum) && gbNum >= 1 && gbNum <= 1024;
  const gbError = gb.trim() === "" || gbValid ? undefined : "Целое от 1 до 1024";

  const daysNum = Number(days.trim());
  const daysValid = Number.isInteger(daysNum) && daysNum >= 1 && daysNum <= 365;
  const daysError = days.trim() === "" || daysValid ? undefined : "Целое от 1 до 365";

  const usesNum = Number(uses.trim());
  const usesValid = Number.isInteger(usesNum) && usesNum >= 1 && usesNum <= 10_000;
  const usesError = uses.trim() === "" || usesValid ? undefined : "Целое от 1 до 10 000";

  const ready = gbValid && daysValid && usesValid;
  const totalGb = ready ? gbNum * usesNum : 0;
  const expiresIso = daysValid
    ? new Date(Date.now() + daysNum * 86_400_000).toISOString()
    : null;

  const create = useMutation({
    mutationFn: () =>
      endpoints.bgiftCreate({
        gb_amount: gbNum,
        validity_days: daysNum,
        max_uses: usesNum,
      }),
    onSuccess: () => {
      toast.success("Ссылка создана — скопируйте её из карточки");
      qc.invalidateQueries({ queryKey: ["bgift"] });
      onClose();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось создать ссылку"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Новая подарочная ссылка"
      description="По переходу человек получает гигабайты обхода. Один человек — одна активация."
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
            Создать ссылку
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field
          label="Гигабайт за активацию"
          value={gb}
          onChange={setGb}
          error={gbError}
          presets={GB_PRESETS}
        />
        <Field
          label="Сколько дней живёт ссылка"
          value={days}
          onChange={setDays}
          error={daysError}
          presets={DAYS_PRESETS}
        />
        <Field
          label="Максимум активаций"
          value={uses}
          onChange={setUses}
          error={usesError}
          presets={USES_PRESETS}
        />

        <div className="rounded-md border border-border bg-bg-subtle p-3 text-base text-fg-muted">
          {ready ? (
            <>
              При полной выборке ссылка раздаст{" "}
              <span className="font-medium text-fg">{fmtNum(totalGb)} ГБ</span> —{" "}
              {fmtNum(gbNum)} ГБ на каждую из {fmtNum(usesNum)} активаций. Работать
              будет до <span className="font-medium text-fg">{fmtDate(expiresIso)}</span>.
              <div className="mt-1 text-xs text-fg-subtle">
                Отсчёт срока начнётся в момент создания, поэтому дата сдвинется
                на то время, что форма открыта.
              </div>
            </>
          ) : (
            "Заполните поля — здесь появится, сколько гигабайт ссылка раздаст при полной выборке."
          )}
        </div>
      </div>
    </Modal>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  presets,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  presets: number[];
}) {
  return (
    <div>
      <Input
        label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        error={error}
      />
      <div className="mt-1 flex flex-wrap gap-1">
        {presets.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onChange(String(p))}
            aria-pressed={value.trim() === String(p)}
            className={cn(
              "rounded-md border px-2 py-0.5 text-xs transition-colors",
              value.trim() === String(p)
                ? "border-accent-9 bg-accent-3 font-medium text-fg"
                : "border-border text-fg-muted hover:text-fg",
            )}
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
