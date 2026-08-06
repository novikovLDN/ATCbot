import { Film, Image as ImageIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import { BUTTON_LABELS } from "./buttonCatalog";

/**
 * Текст рассылки в том виде, в котором его получит человек.
 *
 * ЗАЧЕМ. Отправка необратима, и единственное, что можно проверить до
 * неё, — что написано и как это выглядит. Голый `whitespace-pre-wrap`
 * над HTML-исходником этого не даёт: админ пишет `<b>` и `<blockquote>`,
 * а видит их же угловыми скобками, поэтому сломанную разметку замечают
 * уже после рассылки.
 *
 * ЭТО ТОЛЬКО ПОКАЗ, А НЕ ПРЕОБРАЗОВАНИЕ ПЕРЕД ОТПРАВКОЙ. На сервер
 * уходит ровно то, что человек набрал; premium-эмодзи в HTML-тег
 * превращает `app/utils/telegram_safe.convert_tg_emoji` (в дашбордных
 * маршрутах у него второе имя `normalize_premium_emoji`) — единственная
 * реализация, у неё свои тесты на экранирование метки. Здесь мы её не
 * повторяем и не подменяем: здесь premium-эмодзи просто разворачивается
 * в запасной символ, потому что браузер кастомные эмодзи Telegram
 * рисовать не умеет. Если начать собирать здесь то, что уходит в
 * Telegram, вернётся ошибка, ради которой ту копию и убрали: один
 * символ `&` в тексте ронял разбор сообщения и рассылку целиком.
 *
 * РАЗМЕТКА ПРОПУСКАЕТСЯ ПО БЕЛОМУ СПИСКУ. Прежние две копии `sanitize()`
 * (в списке рассылок и в мастере) работали чёрным списком: вырезали
 * `<script>`, `on*=` и `javascript:`, а всё остальное пускали как есть.
 * Чёрный список полон ровно до следующего способа его обойти. Здесь
 * сначала экранируется всё, потом обратно разворачиваются только те
 * теги, которые понимает сам Telegram.
 */

/** Теги без атрибутов, которые Telegram разбирает в сообщениях. */
const PLAIN_TAGS = "b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote";

function escapeAll(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Экранированный текст → безопасный HTML с разрешённой разметкой.
 * На вход обязан приходить результат `escapeAll`, иначе белый список
 * теряет смысл: в нём разворачиваются только те последовательности,
 * которые экранирование уже сделало безвредными.
 */
function allowTelegramMarkup(escaped: string): string {
  return (
    escaped
      // Раскрывающаяся цитата — отдельным случаем, до простого blockquote:
      // иначе атрибут expandable останется висеть текстом внутри строки.
      .replace(/&lt;blockquote expandable&gt;/gi, '<blockquote data-expandable="1">')
      .replace(new RegExp(`&lt;(/?)(${PLAIN_TAGS})&gt;`, "gi"), "<$1$2>")
      .replace(/&lt;br\s*\/?&gt;/gi, "<br>")
      // Ссылка: только http(s). Схемы javascript: и data: сюда не пройдут
      // не потому, что вычёркиваются, а потому, что не совпадают с шаблоном.
      .replace(
        /&lt;a href=&quot;(https?:\/\/[^"&\s]+)&quot;&gt;/gi,
        '<a href="$1" target="_blank" rel="noopener noreferrer">',
      )
      .replace(/&lt;\/a&gt;/gi, "</a>")
      // Premium-эмодзи в двух видах: готовый тег с сервера и markdown-форма,
      // которую админ набирает руками. И там и там оставляем запасной символ
      // — то, что увидит получатель без Telegram Premium.
      .replace(/&lt;tg-emoji emoji-id=&quot;\d+&quot;&gt;/gi, '<span data-premium="1">')
      .replace(/&lt;\/tg-emoji&gt;/gi, "</span>")
      .replace(
        /!\[([^\]]*)\]\(tg:\/\/emoji\?id=\d+\)/g,
        '<span data-premium="1">$1</span>',
      )
  );
}

/** Текст рассылки → HTML для показа. Экспортируется ради тестируемости
 *  и чтобы обе вкладки считали одинаково. */
export function renderMessage(raw: string): string {
  return allowTelegramMarkup(escapeAll(raw));
}

export function MessagePreview({
  message,
  buttons = [],
  photo,
  animation,
  className,
}: {
  message: string;
  buttons?: string[];
  photo?: boolean;
  animation?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("space-y-3", className)}>
      {/* Медиа помечается словом, а не только иконкой: подпись у фото
          ограничена 1024 символами, и знать, приложено ли оно, важнее,
          чем красиво. */}
      {(photo || animation) && (
        <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-subtle px-3 py-2 text-base text-fg-muted">
          {photo ? (
            <ImageIcon className="h-3.5 w-3.5 shrink-0" aria-hidden />
          ) : (
            <Film className="h-3.5 w-3.5 shrink-0" aria-hidden />
          )}
          {photo ? "Сверху будет фото" : "Сверху будет GIF"}
        </div>
      )}

      <div
        // prose-подобные правила заданы точечно: глобальных стилей под
        // b/i/blockquote в панели нет, а получатель увидит их жирными и
        // с отбивкой.
        className={cn(
          "whitespace-pre-wrap break-words rounded-md border border-border bg-bg-card p-3 text-base leading-relaxed text-fg",
          "[&_b]:font-semibold [&_strong]:font-semibold [&_i]:italic [&_em]:italic",
          "[&_u]:underline [&_ins]:underline [&_s]:line-through [&_del]:line-through [&_strike]:line-through",
          "[&_a]:text-accent-11 [&_a]:underline",
          "[&_code]:rounded [&_code]:bg-bg-subtle [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs",
          "[&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-bg-subtle [&_pre]:p-2 [&_pre]:font-mono [&_pre]:text-xs",
          "[&_blockquote]:my-1 [&_blockquote]:border-l-2 [&_blockquote]:border-border-strong [&_blockquote]:pl-3",
        )}
        // Строка прошла escapeAll + белый список тегов выше. Иначе сюда
        // попадать нечему: другого пути к этому свойству в разделе нет.
        dangerouslySetInnerHTML={{ __html: renderMessage(message) }}
      />

      {buttons.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs text-fg-subtle">
            Кнопки под сообщением, сверху вниз:
          </div>
          <div className="space-y-1">
            {buttons.map((key) => (
              <div
                key={key}
                className="rounded-md border border-border-control bg-bg-subtle px-3 py-1.5 text-center text-base text-fg"
              >
                {BUTTON_LABELS[key] ?? key}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
