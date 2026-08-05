import { useEffect, useState, type ReactNode } from "react";
import { Modal } from "./Modal";
import { Button } from "./Button";
import { Input } from "./Input";

/**
 * Диалог подтверждения.
 *
 * Правила взяты из NN/g и Primer (ux-patterns §2.1–2.3) и соблюдаются буквально:
 *
 *  1. Никаких «вы уверены?». В заголовке и тексте — что именно произойдёт и с
 *     чем: «Вернуть 1 490 ₽ пользователю @ivanov».
 *  2. Кнопки называются действиями, а не «да» и «нет»: «Вернуть 1 490 ₽» и
 *     «Не возвращать». 1–3 слова.
 *  3. Подтверждающая кнопка НЕ получает фокус на открытии: фокус встаёт на
 *     первый элемент окна — крестик закрытия, то есть на отказ от действия.
 *     Предвыбранное «да» превращает диалог в лишний клик, который нажимают на
 *     автопилоте.
 *  4. Для необратимого требуется ввести точный идентификатор объекта (не слово
 *     «УДАЛИТЬ», а имя того, что удаляется — тогда нельзя перепутать объект).
 *     Кнопка неактивна до точного совпадения. Приём GitHub Danger Zone.
 *
 * Когда НЕ показывать этот диалог: на частых и обратимых действиях. Диалог на
 * каждый чих приводит к тому, что его перестают читать. Для обратимого —
 * UndoBanner.
 */
export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  /** Что именно произойдёт. Суммы, имена, количества — цифрами и словами. */
  body,
  confirmLabel,
  cancelLabel = "Отмена",
  /** true — кнопка подтверждения красная (необратимое, разрушительное). */
  destructive,
  /** Точная строка, которую нужно набрать. Обычно ID или имя объекта. */
  requireText,
  /** Подпись над полем ввода: что именно набирать. */
  requireHint,
  loading,
  extra,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  requireText?: string;
  requireHint?: string;
  loading?: boolean;
  /** Дополнительный блок: поле причины, детали последствий. */
  extra?: ReactNode;
}) {
  const [typed, setTyped] = useState("");

  // Сбрасываем ввод при каждом открытии: иначе набранное для прошлого объекта
  // подтвердит следующий.
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  const locked = requireText ? typed.trim() !== requireText : false;

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      // Необратимое не закрывается кликом мимо: промах не должен ни отменять
      // набранное подтверждение, ни выглядеть как отказ от операции.
      dismissible={!requireText}
      footer={
        <>
          {/* Отмена стоит первой в DOM — она же получает фокус при открытии. */}
          <Button onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            onClick={onConfirm}
            disabled={locked}
            loading={loading}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-base text-fg-muted">
        <div>{body}</div>
        {extra}
        {requireText && (
          <Input
            label={requireHint ?? `Введите «${requireText}» для подтверждения`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            mono
            autoComplete="off"
            spellCheck={false}
            placeholder={requireText}
            hint={locked ? "Кнопка включится, когда значение совпадёт полностью" : undefined}
          />
        )}
      </div>
    </Modal>
  );
}
