import { useState } from "react";
import { AlertCircle, CheckCircle2, Send } from "lucide-react";

import { fmtNum } from "@/lib/format";
import { Button, Card, CardBody, CardHeader, ConfirmDialog } from "@/components/ui";
import { MessagePreview } from "./MessagePreview";

/**
 * Последний экран мастера: что уйдёт, скольким, и две защиты от того,
 * чтобы это случилось случайно.
 *
 * ПОЧЕМУ ЗДЕСЬ НЕ ОКНО ОТМЕНЫ. По классификации опасных действий
 * (research §6.6) массовая рассылка — единственная операция раздела,
 * которую нельзя откатить ничем: Telegram не отзывает разосланное, а
 * прочитанное не становится непрочитанным. `UndoBanner`, которым в
 * панели закрыты обратимые действия, обещал бы здесь возврат, которого
 * не существует. Поэтому подтверждение — до, а не отмена — после.
 *
 * ТРИ ВЕЩИ, КОТОРЫЕ ЧЕЛОВЕК ОБЯЗАН УВИДЕТЬ ДО ОТПРАВКИ:
 *   1. сколько именно людей это получит — числом, крупно;
 *   2. какой сегмент — словами, а не ключом;
 *   3. сам текст ровно в том виде, в каком он придёт.
 *
 * ПРОВЕРКА НА СЕБЕ СТОИТ ПЕРЕД КНОПКОЙ ОТПРАВКИ, А НЕ ПОСЛЕ. Она
 * единственная показывает то, чего не покажет предпросмотр: как Telegram
 * разберёт разметку, встанут ли premium-эмодзи и влезет ли подпись под
 * фото. Раньше эта кнопка стояла в один ряд с «Запустить» и выглядела
 * такой же — то есть попадалась на глаза уже после того, как рука
 * потянулась к отправке.
 *
 * ПОДТВЕРЖДЕНИЕ ТРЕБУЕТ НАБРАТЬ ЧИСЛО ПОЛУЧАТЕЛЕЙ. Вторую кнопку
 * нажимают на автопилоте, число — нет, и заодно оно заставляет прочесть
 * то самое число (приём GitHub Danger Zone, ux-patterns §2.3).
 */

export function SendReview({
  segmentLabel,
  audience,
  message,
  buttons,
  photo,
  animation,
  captionOverflow,
  onTest,
  testing,
  testedAt,
  onSend,
  sending,
}: {
  segmentLabel: string;
  /** null — размер аудитории неизвестен. Отправка запрещена. */
  audience: number | null;
  message: string;
  buttons: string[];
  photo: boolean;
  animation: boolean;
  /** Подпись под фото длиннее 1024 символов — Telegram не примет. */
  captionOverflow: boolean;
  onTest: () => void;
  testing: boolean;
  /** Время последней успешной проверки на себе. */
  testedAt: Date | null;
  onSend: () => void;
  sending: boolean;
}) {
  const [confirming, setConfirming] = useState(false);

  const blocked =
    audience == null || audience === 0 || captionOverflow || message.trim() === "";

  return (
    <div className="space-y-4">
      {/* 1. Кому. Число — самое крупное на экране: это то, что человек
             перепроверяет в последнюю секунду. */}
      <Card>
        <CardHeader title="Кому уйдёт" />
        <CardBody>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div className="min-w-0">
              <div className="text-base text-fg-muted">Сегмент</div>
              <div className="text-lg font-medium text-fg">{segmentLabel}</div>
            </div>
            <div className="text-right">
              <div className="text-base text-fg-muted">Получателей</div>
              <div className="text-3xl font-semibold tabular-nums text-fg">
                {audience == null ? "неизвестно" : fmtNum(audience)}
              </div>
            </div>
          </div>

          {audience == null && (
            <Warning>
              Размер этого сегмента посчитать не удалось. Отправлять, не зная
              скольким, нельзя — вернитесь на шаг «Кому» и обновите список.
            </Warning>
          )}
          {audience === 0 && (
            <Warning>
              В этом сегменте сейчас никого. Отправлять некому — выберите другую
              аудиторию.
            </Warning>
          )}
          {audience != null && audience > 0 && (
            <p className="mt-3 text-xs text-fg-muted">
              Точный состав пересчитывается в момент отправки, поэтому итог может
              отличаться на несколько человек.
            </p>
          )}
        </CardBody>
      </Card>

      {/* 2. Что получат. */}
      <Card>
        <CardHeader
          title="Что получат люди"
          subtitle="так это придёт в Telegram"
        />
        <CardBody>
          <MessagePreview
            message={message}
            buttons={buttons}
            photo={photo}
            animation={animation}
          />
          {captionOverflow && (
            <Warning>
              Текст длиннее 1024 символов, а подпись под фото столько не
              вмещает — массовая рассылка упадёт целиком. Уберите фото или
              сократите текст.
            </Warning>
          )}
        </CardBody>
      </Card>

      {/* 3. Проверка на себе — отдельным блоком и до кнопки отправки. */}
      <Card>
        <CardHeader
          title="Сначала проверьте на себе"
          subtitle="придёт только вам, в базе ничего не останется"
        />
        <CardBody className="space-y-2">
          <p className="text-base text-fg-muted">
            Предпросмотр выше рисует браузер, а сообщение разбирает Telegram.
            Разъезжаются они именно там, где дороже всего: сломанный тег,
            premium-эмодзи, не влезшая подпись под фото.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              icon={<Send className="h-3.5 w-3.5" />}
              onClick={onTest}
              loading={testing}
              disabled={sending || message.trim() === ""}
            >
              Отправить себе
            </Button>
            {testedAt && (
              <span className="inline-flex items-center gap-1.5 text-base text-success">
                <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
                отправлено в{" "}
                {testedAt.toLocaleTimeString("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}{" "}
                — посмотрите свой чат
              </span>
            )}
          </div>
        </CardBody>
      </Card>

      {/* 4. Отправка. Кнопка красная: действие необратимо, и это должно
             читаться до нажатия, а не после. */}
      <div className="flex flex-wrap items-center justify-end gap-3 rounded-lg border border-border bg-bg-card p-4">
        <p className="mr-auto max-w-md text-base text-fg-muted">
          Отправку нельзя остановить и нельзя отозвать: сообщение уйдёт живым
          людям.
        </p>
        <Button
          variant="danger"
          icon={<Send className="h-3.5 w-3.5" />}
          onClick={() => setConfirming(true)}
          disabled={blocked}
          loading={sending}
        >
          {audience == null
            ? "Отправить"
            : `Отправить ${fmtNum(audience)} получателям`}
        </Button>
      </div>

      <ConfirmDialog
        open={confirming}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          onSend();
        }}
        title="Отправить рассылку"
        body={
          <>
            Сообщение уйдёт <b className="text-fg">{fmtNum(audience ?? 0)}</b>{" "}
            получателям из сегмента «{segmentLabel}» прямо сейчас. Остановить
            отправку на середине нельзя, отозвать отправленное — тоже.
            {!testedAt && (
              <span className="mt-2 block text-danger">
                Вы ещё не отправляли это себе. Разметку и premium-эмодзи никто не
                проверял.
              </span>
            )}
          </>
        }
        confirmLabel={`Отправить ${fmtNum(audience ?? 0)}`}
        cancelLabel="Не отправлять"
        destructive
        requireText={String(audience ?? 0)}
        requireHint={`Наберите число получателей — ${audience ?? 0}`}
        loading={sending}
      />
    </div>
  );
}

function Warning({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-3 flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-3 text-base text-danger">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}
