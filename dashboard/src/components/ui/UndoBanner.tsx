import { useEffect, useRef, useState, type ReactNode } from "react";
import { Undo2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/**
 * Баннер отмены действия.
 *
 * ПОЧЕМУ БАННЕР, А НЕ ТОСТ. Тост, который сам исчезает через несколько секунд и
 * содержит внутри кнопку, нарушает WCAG 2.2.1 Timing Adjustable (уровень A), а
 * заодно 2.1.1, 2.4.3 и 1.3.2: интерактив без управления фокусом, сообщение в
 * конце DOM, исчезающее до того, как до него доберутся с клавиатуры
 * (Adrian Roselli, ux-patterns §2.4). Рекомендация — сделать сообщение
 * неисчезающим и перевести на него фокус, то есть превратить в немодальный
 * диалог. Ровно это здесь и сделано.
 *
 * Баннер живёт до тех пор, пока пользователь не нажмёт «Отменить» или не
 * закроет его сам. Обратный отсчёт показывает, сколько осталось до того, как
 * операция уйдёт на сервер; когда время вышло, баннер меняет текст, а не
 * пропадает молча — иначе непонятно, случилось ли что-нибудь вообще.
 *
 * Куда ставить: в шапку списка, над данными, к которым относится действие.
 * Не в угол экрана.
 *
 * Где применять (ux-patterns, сводка): отзыв доступа, выдача гифта, массовые
 * правки — всё обратимое. Для необратимого (возврат денег провайдеру, отправка
 * рассылки) отмены недостаточно, нужен ConfirmDialog.
 */
export function UndoBanner({
  open,
  message,
  /** Сколько секунд действие ждёт перед отправкой. Gmail даёт на выбор
   *  5/10/20/30 — единственная публично откалиброванная величина. */
  seconds = 10,
  onUndo,
  onExpire,
  onDismiss,
  className,
}: {
  open: boolean;
  message: ReactNode;
  seconds?: number;
  onUndo: () => void;
  /** Время вышло — здесь вызывается фактическая отправка на сервер. */
  onExpire?: () => void;
  onDismiss?: () => void;
  className?: string;
}) {
  const [left, setLeft] = useState(seconds);
  const ref = useRef<HTMLDivElement>(null);
  const expiredRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    setLeft(seconds);
    expiredRef.current = false;
    // Фокус переводится на баннер: сообщение с кнопкой обязано быть достижимо
    // с клавиатуры сразу, а не после обхода всей страницы.
    ref.current?.focus();
    const t = window.setInterval(() => {
      setLeft((v) => {
        if (v <= 1) {
          window.clearInterval(t);
          if (!expiredRef.current) {
            expiredRef.current = true;
            onExpire?.();
          }
          return 0;
        }
        return v - 1;
      });
    }, 1000);
    return () => window.clearInterval(t);
    // onExpire намеренно не в зависимостях: пересоздание таймера на каждый
    // рендер родителя сбрасывало бы отсчёт.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, seconds]);

  if (!open) return null;

  const done = left === 0;

  return (
    <div
      ref={ref}
      tabIndex={-1}
      // role="status" + aria-live=polite: живая область существует в DOM
      // заранее и озвучивается, не перебивая пользователя.
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-3 rounded-md border border-accent-7 bg-accent-3 px-3 py-2 outline-none",
        className,
      )}
    >
      <Undo2 className="h-4 w-4 shrink-0 text-accent-text" aria-hidden />
      <div className="min-w-0 flex-1 text-base text-fg">
        {message}
        {!done && (
          <span className="ml-2 tabular-nums text-fg-muted">
            отменить можно ещё {left} с
          </span>
        )}
      </div>
      {!done ? (
        <Button size="sm" onClick={onUndo}>
          Отменить
        </Button>
      ) : (
        <span className="text-xs text-fg-muted">Время на отмену вышло</span>
      )}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Скрыть сообщение"
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
    </div>
  );
}
