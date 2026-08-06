import type { LucideIcon } from "lucide-react";
import { Inbox, SearchX, Lock, AlertTriangle, CheckCircle2, PlugZap } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/**
 * Пустые состояния. Их ШЕСТЬ, и это разные компоненты, а не один с разным
 * текстом (ux-patterns §3.4, research §6.4).
 *
 * КАКОЙ КОГДА — решается одним вопросом: «почему здесь пусто?». Ответы не
 * пересекаются, поэтому и компоненты разные. Порядок проверки при написании
 * экрана — сверху вниз, первый подошедший и берём:
 *
 *   1. Запрос не вернулся (isError, таймаут, 5xx)     → EmptyFailure
 *   2. Прав на раздел нет (403 по роли)               → EmptyNoAccess
 *   3. Функция/интеграция не подключена               → EmptyNotConfigured
 *   4. Пусто, и это хорошая новость                   → EmptyAllClear
 *   5. Активен фильтр или поиск                       → EmptyFilter
 *   6. Ничего и никогда не заводили                   → EmptyFirstRun
 *
 * Подробно, с примерами наших экранов:
 *
 *   EmptyFirstRun     — сущностей ещё нет вообще: «Промокодов пока нет».
 *                       Объясняем, зачем раздел, и даём кнопку создания.
 *                       НЕ подходит, когда включён фильтр (см. следующий).
 *   EmptyFilter       — данные есть, но фильтр или поиск не совпал: «За
 *                       выбранный период платежей нет». Кнопка «создать»
 *                       здесь ЗАПРЕЩЕНА: это классическая ошибка — человек
 *                       искал существующее, а ему предлагают завести новое.
 *                       Правильное действие — сбросить фильтр.
 *   EmptyAllClear     — пусто, и это ХОРОШО: «Расхождений с VPN-панелью нет»,
 *                       «Очередь провизии пуста». Единственное состояние с
 *                       зелёным тоном, и единственное, где ничего делать не
 *                       надо. Путать его с EmptyFirstRun нельзя: там «ещё
 *                       ничего не начиналось», здесь «всё сошлось».
 *   EmptyNotConfigured— сама функция ещё не настроена: ключи Remnawave не
 *                       заданы, push на устройстве не подключён. Отличается
 *                       от EmptyFirstRun тем, что записей не будет вовсе,
 *                       пока не выполнена настройка, — и действие тут
 *                       «Настроить», а не «Создать».
 *   EmptyNoAccess     — прав не хватает. Без обвинения, с указанием, к кому
 *                       идти. Кнопки «повторить» здесь нет: повтор ничего не
 *                       изменит.
 *   EmptyFailure      — не загрузилось. Говорим, ЧТО именно не загрузилось, и
 *                       даём «Повторить» на месте — виджет держит свои
 *                       габариты и не блокирует остальную панель (§3.5).
 *
 * ГЛАВНОЕ ПРАВИЛО, из-за которого всё это заведено: отказ запроса нельзя
 * рисовать любым из первых пяти. «Платежей нет» вместо «сервер не ответил» —
 * успокаивающая неправда, и это главный дефект старого дашборда. Если у
 * запроса есть isError, ветка с EmptyFailure обязана стоять ПЕРВОЙ.
 *
 * Тон везде один: не заставлять чувствовать себя виноватым (Polaris).
 *
 * «Повторить» имеет смысл только при временных отказах — таймаут, обрыв, 5xx.
 * При ошибке клиента (422, 403) повтор бессмыслен и вводит в заблуждение
 * (§3.6, пункт 5), поэтому у EmptyFailure отдельный флаг retryable.
 */

function Shell({
  icon: Icon,
  title,
  description,
  action,
  tone = "neutral",
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  tone?: "neutral" | "danger" | "success";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-border px-6 py-10 text-center",
        className,
      )}
    >
      <div
        className={cn(
          "grid h-10 w-10 place-items-center rounded-lg",
          tone === "danger"
            ? "bg-danger/12 text-danger"
            : tone === "success"
              ? "bg-success/12 text-success"
              : "bg-bg-subtle text-fg-subtle",
        )}
      >
        <Icon className="h-5 w-5" aria-hidden />
      </div>
      <div className="max-w-sm">
        <div className="text-base font-medium text-fg">{title}</div>
        {description && <div className="mt-1 text-base text-fg-muted">{description}</div>}
      </div>
      {action}
    </div>
  );
}

/** Случай 1: раздел пуст, потому что в нём ещё ничего не заводили. */
export function EmptyFirstRun({
  title,
  description,
  actionLabel,
  onAction,
  icon = Inbox,
  className,
}: {
  title: string;
  /** Зачем нужен раздел и что даст первая запись. */
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <Shell
      icon={icon}
      title={title}
      description={description}
      className={className}
      action={
        actionLabel && onAction ? (
          <Button variant="primary" onClick={onAction}>
            {actionLabel}
          </Button>
        ) : undefined
      }
    />
  );
}

/** Случай 2: не совпал фильтр или поиск. Никаких «создать». */
export function EmptyFilter({
  /** Что именно искали — строка запроса или перечисление активных фильтров. */
  query,
  onReset,
  className,
}: {
  query?: string;
  onReset: () => void;
  className?: string;
}) {
  return (
    <Shell
      icon={SearchX}
      title="Ничего не нашлось"
      description={
        query
          ? `По условию «${query}» записей нет. Проверьте написание или снимите часть фильтров.`
          : "Под выбранные фильтры не попала ни одна запись. Снимите часть условий."
      }
      className={className}
      action={<Button onClick={onReset}>Сбросить фильтры</Button>}
    />
  );
}

/**
 * Случай 3: пусто — и это хорошая новость.
 *
 * «Расхождений с панелью нет», «Очередь провизии пуста», «Ничего не требует
 * внимания». Единственное пустое состояние, где от человека ничего не ждут,
 * поэтому здесь нет кнопки действия по умолчанию и стоит зелёный тон.
 *
 * Почему отдельный компонент, а не EmptyFirstRun с другим текстом: смысл
 * противоположный. «Промокодов пока нет» — приглашение начать. «Расхождений
 * нет» — отчёт о том, что проверка прошла. Если написать это одинаково серым
 * «Ничего не найдено», человек не поймёт, всё хорошо или сломалось.
 *
 * ВАЖНО: показывать только тогда, когда проверка ДЕЙСТВИТЕЛЬНО прошла. Если
 * запрос упал — это EmptyFailure. Зелёная галочка на неотвеченном запросе —
 * ровно та неправда, ради которой всё это разделение и заведено.
 */
export function EmptyAllClear({
  title,
  /** Что именно проверили и что это значит. Одно-два предложения. */
  description,
  /** Необязательное действие: «Проверить ещё раз», «Открыть журнал». */
  actionLabel,
  onAction,
  icon = CheckCircle2,
  className,
}: {
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <Shell
      icon={icon}
      tone="success"
      title={title}
      description={description}
      className={className}
      action={
        actionLabel && onAction ? (
          <Button size="sm" onClick={onAction}>
            {actionLabel}
          </Button>
        ) : undefined
      }
    />
  );
}

/**
 * Случай 4: функция ещё не настроена.
 *
 * Отличие от EmptyFirstRun: там записей нет, но завести их можно прямо сейчас
 * кнопкой рядом. Здесь записей не будет вовсе, пока не выполнена настройка —
 * не заданы ключи интеграции, не подключён push на устройстве, не выдан токен
 * панели. Кнопка «Создать» тут вводила бы в заблуждение: создавать пока негде.
 *
 * Поэтому описание обязано называть КОНКРЕТНЫЙ шаг настройки, а не «обратитесь
 * к администратору».
 */
export function EmptyNotConfigured({
  title,
  /** Конкретный шаг: «Задайте REMNAWAVE_API_URL и токен в переменных окружения». */
  description,
  actionLabel,
  onAction,
  icon = PlugZap,
  className,
}: {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <Shell
      icon={icon}
      title={title}
      description={description}
      className={className}
      action={
        actionLabel && onAction ? (
          <Button variant="primary" size="sm" onClick={onAction}>
            {actionLabel}
          </Button>
        ) : undefined
      }
    />
  );
}

/** Случай 5: доступа нет. Без вины и без предложения «попробовать ещё раз». */
export function EmptyNoAccess({
  what = "этому разделу",
  contact,
  className,
}: {
  /** «этому разделу», «журналу действий», «настройкам выплат». */
  what?: string;
  /** Кто выдаёт доступ. Без этого сообщение — тупик. */
  contact?: string;
  className?: string;
}) {
  return (
    <Shell
      icon={Lock}
      title={`Нет доступа к ${what}`}
      description={
        contact
          ? `Доступ выдаёт ${contact}. Напишите ему, чтобы открыть раздел.`
          : "Доступ выдаёт владелец панели."
      }
      className={className}
    />
  );
}

/** Случай 6: отказ загрузки. Виджет держит габариты, панель не блокируется. */
export function EmptyFailure({
  /** Что именно не загрузилось: «платежи за 30 дней», «список клиентов». */
  what,
  /** Причина человеческим языком, без кода ошибки. */
  reason,
  onRetry,
  /** Повтор имеет смысл только при временном отказе. */
  retryable = true,
  className,
}: {
  what: string;
  reason?: string;
  onRetry?: () => void;
  retryable?: boolean;
  className?: string;
}) {
  return (
    <Shell
      icon={AlertTriangle}
      tone="danger"
      title={`Не удалось загрузить: ${what}`}
      description={reason ?? "Сервер не ответил вовремя. Остальная панель работает."}
      className={className}
      action={
        retryable && onRetry ? <Button onClick={onRetry}>Повторить</Button> : undefined
      }
    />
  );
}
