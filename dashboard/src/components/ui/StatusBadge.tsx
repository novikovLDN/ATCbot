import { Check, Clock, AlertTriangle, XCircle, Info, Minus, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Бейдж состояния.
 *
 * Ровно шесть состояний, ни одним больше (research §10.3). Раньше в конфиге
 * жили пять «tag»-цветов плюс info, special, success, danger, warning, accent,
 * secondary — тринадцать цветовых сущностей на шесть смыслов.
 *
 * Каждое состояние = цвет + иконка + слово. Это не украшение: цвет не может
 * быть единственным каналом передачи информации, красно-зелёная дальтонизация
 * самая распространённая (Carbon, research §4.11).
 *
 * Иконка помечена aria-hidden — её смысл уже сказан словом рядом, и дублировать
 * его в скринридере незачем.
 */

export type StatusKind =
  | "success" // оплачено, выполнено
  | "pending" // ожидание, в обработке
  | "risk" // риск, просрочено
  | "failure" // отказ, возврат
  | "info" // информация, система
  | "neutral"; // нейтральное, архив

// Сплошной бейдж красится парой «цвет состояния + цвет карточки», а не
// «цвет + белый»: в тёмной теме семантические токены светлые, и белый текст на
// них не читался бы. text-bg-card переворачивается вместе с темой сам.
const MAP: Record<StatusKind, { icon: LucideIcon; cls: string; solidCls: string }> = {
  success: { icon: Check, cls: "bg-success/12 text-success", solidCls: "bg-success text-bg-card" },
  pending: { icon: Clock, cls: "bg-warning/12 text-warning", solidCls: "bg-warning text-bg-card" },
  risk: { icon: AlertTriangle, cls: "bg-risk/12 text-risk", solidCls: "bg-risk text-bg-card" },
  failure: { icon: XCircle, cls: "bg-danger/12 text-danger", solidCls: "bg-danger text-bg-card" },
  info: { icon: Info, cls: "bg-info/12 text-info", solidCls: "bg-info text-bg-card" },
  neutral: { icon: Minus, cls: "bg-bg-subtle text-fg-muted", solidCls: "bg-n-12 text-bg-card" },
};

export function StatusBadge({
  kind,
  children,
  solid,
  className,
}: {
  kind: StatusKind;
  /** Слово. Обязательно: бейдж без текста — это просто цветное пятно. */
  children: React.ReactNode;
  /** Сплошная заливка — для случаев, когда бейдж лежит на цветной подложке.
   *  Пара «белый текст на тёмной заливке» проверена на 4.5:1. */
  solid?: boolean;
  className?: string;
}) {
  const { icon: Icon, cls, solidCls } = MAP[kind];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium",
        solid ? solidCls : cls,
        className,
      )}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      {children}
    </span>
  );
}

/**
 * Точка состояния для плотной таблицы, где на бейдж нет места.
 * Слово всё равно обязано быть рядом — либо в соседней ячейке, либо в title.
 */
export function StatusDot({ kind, label }: { kind: StatusKind; label: string }) {
  const solidByKind: Record<StatusKind, string> = {
    success: "bg-success-solid",
    pending: "bg-warning-solid",
    risk: "bg-risk-solid",
    failure: "bg-danger-solid",
    info: "bg-info-solid",
    neutral: "bg-n-9",
  };
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("h-2 w-2 shrink-0 rounded-full", solidByKind[kind])} aria-hidden />
      <span className="text-xs text-fg-muted">{label}</span>
    </span>
  );
}
