import { cn } from "@/lib/cn";
import { fmtDate, fmtRelative } from "@/lib/format";
import { expiryOf, usageOf } from "./labels";

/**
 * «Сколько уже потрачено и когда истекает» — одна пара строк на три
 * вкладки: промокоды, промо-ссылки, подарочные ГБ.
 *
 * ЗАЧЕМ ЭТО ВООБЩЕ. Раньше экраны сообщали факт существования: «код
 * SUMMER25, −20 %, активен». Из этого не следует ничего: работает он ещё
 * неделю или кончится к обеду — не видно. Лимит без расхода и срок без
 * остатка — числа, из которых не следует действие.
 *
 * ЦВЕТ НЕ ЕДИНСТВЕННЫЙ НОСИТЕЛЬ. Полоска заполнения дублируется числом
 * «37 из 100», близкий конец подписан словами, истёкший срок — тоже.
 * Уберёте текст, оставив красную полоску, — сломаете экран для
 * дальтоника и для чёрно-белой печати.
 */
export function UsageMeter({
  used,
  max,
  /** Что считаем: «применений», «активаций», «переходов». */
  noun = "применений",
  expiresAt,
  className,
}: {
  used: number | null | undefined;
  max: number | null | undefined;
  noun?: string;
  expiresAt?: string | null;
  className?: string;
}) {
  const use = usageOf(used, max);
  const exp = expiryOf(expiresAt);

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-fg-muted">
          {noun}: <span className="font-medium tabular-nums text-fg">{use.label}</span>
        </span>
        {use.exhausted && <span className="font-medium text-fg-muted">лимит выбран</span>}
      </div>

      {use.ratio !== null && (
        <div
          className="h-1 w-full overflow-hidden rounded-full bg-bg-subtle"
          role="progressbar"
          aria-valuenow={Math.round(use.ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Израсходовано ${use.label}`}
        >
          <div
            className={cn(
              "h-full rounded-full",
              use.exhausted ? "bg-n-9" : use.ratio > 0.8 ? "bg-risk-solid" : "bg-accent-9",
            )}
            style={{ width: `${Math.max(2, use.ratio * 100)}%` }}
          />
        </div>
      )}

      {expiresAt !== undefined && (
        <div className="text-2xs text-fg-subtle">
          {exp.at === null ? (
            "срок не ограничен"
          ) : exp.expired ? (
            <>истёк {fmtRelative(expiresAt)} · {fmtDate(expiresAt)}</>
          ) : (
            <>
              {exp.soon ? (
                <span className="font-medium text-risk">истекает {fmtRelative(expiresAt)}</span>
              ) : (
                <>истекает {fmtRelative(expiresAt)}</>
              )}{" "}
              · {fmtDate(expiresAt)}
            </>
          )}
        </div>
      )}
    </div>
  );
}
