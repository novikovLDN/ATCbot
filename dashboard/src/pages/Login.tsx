import { useEffect, useState } from "react";
import { Fingerprint } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { isInAppBrowser, isPasskeySupported, loginWithPasskey } from "@/lib/passkey";
import { Spinner } from "@/components/Spinner";
import { AuthFrame, Field, FormError, RevealButton } from "@/components/AuthFrame";

export function Login({ onDone }: { onDone: () => void }) {
  const [passkeyAvailable, setPasskeyAvailable] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [inApp] = useState(isInAppBrowser);
  const [copied, setCopied] = useState(false);
  // Plain dashboard address — never the magic link (its token was already
  // taken out of the URL by captureMagicLink).
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/dashboard/`);
      setCopied(true);
    } catch {
      setErr(`Скопируйте адрес вручную: ${window.location.origin}/dashboard/`);
    }
  };

  useEffect(() => {
    let mounted = true;
    endpoints
      .authStatus()
      .then((s) => {
        if (mounted) setPasskeyAvailable(!!s.has_passkey && isPasskeySupported());
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  const explain = (e: unknown, fallback: string) => {
    const ae = e as ApiError;
    if (ae?.status === 429) return "Слишком много попыток. Вход закрыт на 15 минут.";
    if (ae?.status === 401) return "Неверный логин или пароль.";
    if (ae?.status === 409) return "Пароль ещё не установлен. Откройте ссылку из /admin в боте.";
    if (ae?.status === 403) return "Запрос отклонён: откройте дашборд по его адресу, а не со стороннего сайта.";
    return ae?.detail ?? fallback;
  };

  const onPasskey = async () => {
    setPasskeyBusy(true);
    setErr(null);
    try {
      await loginWithPasskey();
      onDone();
    } catch (e: unknown) {
      if ((e as ApiError)?.detail !== "cancelled") setErr(explain(e, "Не удалось войти по ключу устройства."));
    } finally {
      setPasskeyBusy(false);
    }
  };

  const canSubmit = username.trim().length > 0 && password.length > 0 && !busy;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      await endpoints.authLogin({ username: username.trim(), password });
      onDone();
    } catch (e: unknown) {
      setErr(explain(e, "Не удалось войти."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame
      title="Вход"
      sub="Логин и пароль или ключ этого устройства. Сессия действует 5 дней."
      footer={
        <>
          Забыли пароль? Отправьте боту <code className="rounded-full bg-tile-3 px-2 py-0.5 font-mono">/admin</code>,
          нажмите «Сбросить пароль» и откройте новую ссылку в течение 15 минут.
        </>
      }
    >
      {inApp && (
        <div className="tile tile-raised mb-5 p-4 text-[13px] leading-5" role="note">
          <p className="font-medium">Face ID и Touch ID здесь не работают</p>
          <p className="t-mute mt-1">
            Это встроенный браузер приложения (например, Telegram): iOS не даёт ему ключи входа. Нажмите
            «⋯» или значок компаса и выберите «Открыть в Safari» — или войдите паролем ниже.
          </p>
          <button type="button" className="btn-secondary mt-3 min-h-[44px]" onClick={copyLink}>
            {copied ? "Ссылка скопирована" : "Скопировать адрес дашборда"}
          </button>
        </div>
      )}
      {passkeyAvailable && (
        <>
          <button type="button" onClick={onPasskey} disabled={passkeyBusy} className="btn-secondary w-full py-3">
            {passkeyBusy ? <Spinner /> : <Fingerprint className="h-4 w-4" aria-hidden="true" />}
            Войти по Face ID или Touch ID
          </button>
          <div className="t-mute my-5 flex items-center gap-3 text-[12px]" aria-hidden="true">
            <span className="h-px flex-1 bg-tile-4" />
            или паролем
            <span className="h-px flex-1 bg-tile-4" />
          </div>
        </>
      )}
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Логин">
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="username"
            spellCheck={false}
            required
            autoFocus
          />
        </Field>
        <Field label="Пароль">
          <input
            className="input pr-11"
            type={showPwd ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          <RevealButton shown={showPwd} onToggle={() => setShowPwd((v) => !v)} />
        </Field>
        {err && <FormError>{err}</FormError>}
        <button type="submit" disabled={!canSubmit} className="btn-primary mt-1 w-full py-3">
          {busy && <Spinner />}
          Войти
        </button>
      </form>
    </AuthFrame>
  );
}
