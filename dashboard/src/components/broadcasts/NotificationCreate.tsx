import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints } from "@/lib/api";
import { toast } from "@/store/toast";
import { Button, Input, Modal } from "@/components/ui";
import { CATEGORIES } from "./categories";
import { MessagePreview } from "./MessagePreview";

/**
 * Своя заготовка уведомления.
 *
 * ЧТО ЭТО ТАКОЕ И ЧЕГО ОНО НЕ ДЕЛАЕТ. Созданное здесь уведомление бот
 * сам не отправит: у него нет триггера в коде, только текст. Это
 * заготовка, которую можно править и отправлять себе. Прежний экран
 * называл это «Новое» и объяснял разницу в мелком тексте внизу — то
 * есть после того, как форму уже заполнили.
 *
 * КЛЮЧ ПРОВЕРЯЕТСЯ ЗДЕСЬ ЖЕ, А НЕ ТОЛЬКО НА СЕРВЕРЕ. Формат
 * `namespace.name` — требование сервера; сказать о нём после отправки
 * формы значит потратить чужой заполненный текст.
 */

const KEY_RE = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/;

export function NotificationCreate({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("admin.");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("other");
  const [text, setText] = useState("");

  const keyError =
    key.trim() && !KEY_RE.test(key.trim().toLowerCase())
      ? "Нужен вид «раздел.имя»: маленькие латинские буквы, цифры и подчёркивание"
      : undefined;
  const ready =
    KEY_RE.test(key.trim().toLowerCase()) && title.trim().length >= 2 && text.trim() !== "";

  const create = useMutation({
    mutationFn: () =>
      endpoints.automatedNotificationCreate({
        key: key.trim().toLowerCase(),
        title: title.trim(),
        description: description.trim() || undefined,
        category,
        default_text_ru: text,
      }),
    onSuccess: () => {
      toast.success(`«${title.trim()}» создано`);
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось создать"),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title="Своя заготовка уведомления"
      description="Бот не будет отправлять её сам — у неё нет триггера. Текст можно править и отправлять себе."
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={() => create.mutate()}
            disabled={!ready}
            loading={create.isPending}
          >
            Создать
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Input
          label="Ключ"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          mono
          autoComplete="off"
          spellCheck={false}
          placeholder="admin.letnyaya_akciya"
          error={keyError}
          hint={!keyError ? "Например, admin.letnyaya_akciya — по нему уведомление ищут в коде и в API" : undefined}
          autoFocus
        />

        <Input
          label="Название для админки"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Летняя акция, приветствие"
          hint="Так эта заготовка будет называться в списке"
        />

        <Input
          label="Пояснение, необязательно"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Для новых пользователей, летний оффер"
        />

        <div>
          <label
            htmlFor="notif-new-category"
            className="mb-1.5 block text-base font-medium text-fg"
          >
            Раздел
          </label>
          <select
            id="notif-new-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="h-9 w-full rounded-md border border-border-control bg-bg-card px-2 text-base text-fg outline-none focus-visible:border-accent-9"
          >
            {CATEGORIES.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="notif-new-text"
            className="mb-1.5 block text-base font-medium text-fg"
          >
            Текст
          </label>
          <textarea
            id="notif-new-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={8}
            placeholder="<b>Летняя акция!</b>&#10;&#10;Скидка 20% на любой тариф до конца недели."
            className="w-full resize-y rounded-md border border-border-control bg-bg-card p-3 font-mono text-xs leading-relaxed text-fg outline-none focus-visible:border-accent-9"
          />
        </div>

        {text.trim() !== "" && (
          <div>
            <h3 className="mb-1.5 text-base font-medium text-fg">
              Так это увидит человек
            </h3>
            <MessagePreview message={text} />
          </div>
        )}
      </div>
    </Modal>
  );
}
