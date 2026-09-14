import { useState } from "react";
import { ApiError, endpoints } from "@/lib/api";
import { Spinner } from "@/components/Spinner";
import { AuthFrame, Field, FormError, RevealButton } from "@/components/AuthFrame";

export function SetupPassword({ bootstrapToken, onDone }: { bootstrapToken: string; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const usernameOk = /^[a-zA-Z0-9._-]{3,40}$/.test(username);
  const passwordOk = password.length >= 8;
  const confirmOk = confirm === password && password.length > 0;
  const canSubmit = usernameOk && passwordOk && confirmOk && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      await endpoints.authSetup({ username: username.trim(), password, bootstrap_token: bootstrapToken });
      onDone();
    } catch (e: unknown) {
      const ae = e as ApiError;
      if (ae?.status === 409) setErr("Пароль уже установлен. Чтобы сменить, нажмите «Сбросить пароль» в /admin.");
      else if (ae?.status === 401) setErr("Ссылка устарела: она действует 15 минут. Отправьте /admin в боте ещё раз.");
      else if (ae?.status === 429) setErr("Слишком много попыток. Подождите 15 минут.");
      else setErr(ae?.detail ?? "Не удалось сохранить.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame
      title="Первая настройка"
      sub="Придумайте логин и пароль. Дальше вход только по ним или по ключу устройства."
      footer="Ссылка из /admin нужна только сейчас. После сохранения она перестанет открывать дашборд, даже у вас."
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Логин" hint={username && !usernameOk ? "3–40 символов: латиница, цифры, точка, дефис, подчёркивание." : undefined}>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="username"
            spellCheck={false}
            required
            minLength={3}
            maxLength={40}
            autoFocus
          />
        </Field>
        <Field label="Пароль, не короче 8 символов">
          <input
            className="input pr-11"
            type={showPwd ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={200}
          />
          <RevealButton shown={showPwd} onToggle={() => setShowPwd((v) => !v)} />
        </Field>
        <Field label="Пароль ещё раз" hint={confirm && !confirmOk ? "Пароли не совпадают." : undefined}>
          <input
            className="input"
            type={showPwd ? "text" : "password"}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>
        {err && <FormError>{err}</FormError>}
        <button type="submit" disabled={!canSubmit} className="btn-primary mt-1 w-full py-3">
          {busy && <Spinner />}
          Сохранить и войти
        </button>
      </form>
    </AuthFrame>
  );
}
