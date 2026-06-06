import { useEffect, useState } from "react";
import { ShieldCheck, Bot, Lock, User, Eye, EyeOff, Fingerprint } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { isPasskeySupported, loginWithPasskey } from "@/lib/passkey";
import { Spinner } from "@/components/Spinner";

export function Login({ onDone }: { onDone: () => void }) {
  const [passkeyAvailable, setPasskeyAvailable] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);

  useEffect(() => {
    let mounted = true;
    endpoints
      .authStatus()
      .then((s) => {
        if (mounted) setPasskeyAvailable(!!s.has_passkey && isPasskeySupported());
      })
      .catch(() => {
        //
      });
    return () => {
      mounted = false;
    };
  }, []);

  const onPasskey = async () => {
    setPasskeyBusy(true);
    try {
      await loginWithPasskey();
      onDone();
    } catch (e: unknown) {
      const ae = e as ApiError;
      if (ae?.detail === "cancelled") {
        // user dismissed the OS prompt — silent
      } else {
        // surface a friendly message
        alert(ae?.detail ?? "Не удалось войти через passkey");
      }
    } finally {
      setPasskeyBusy(false);
    }
  };

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
      const ae = e as ApiError;
      if (ae?.status === 401) {
        setErr("Неверный логин или пароль");
      } else if (ae?.status === 409) {
        setErr("Пароль ещё не установлен. Зайди по ссылке из /admin.");
      } else {
        setErr(ae?.detail ?? "Не удалось войти");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-full place-items-center px-6 py-12">
      <div className="card relative w-full max-w-md overflow-hidden p-8 animate-slide-up">
        <div className="pointer-events-none absolute -top-32 left-1/2 h-64 w-64 -translate-x-1/2 rounded-full bg-accent/15 blur-3xl" />
        <div className="relative">
          <div className="mb-6 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-secondary text-bg shadow-glow">
            <ShieldCheck className="h-5 w-5" strokeWidth={2.5} />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-fg">
            Atlas Admin
          </h1>
          <p className="mt-2 text-sm text-fg-muted">
            Войди по логину и паролю. Сессия будет действовать <b>5 дней</b>.
          </p>

          {passkeyAvailable && (
            <div className="mt-5">
              <button
                type="button"
                onClick={onPasskey}
                disabled={passkeyBusy}
                className="btn-secondary w-full"
              >
                {passkeyBusy ? (
                  <Spinner />
                ) : (
                  <Fingerprint className="h-3.5 w-3.5 text-accent" />
                )}
                Войти через Face ID / Touch ID
              </button>
              <div className="my-4 flex items-center gap-2 text-[11px] uppercase tracking-wider text-fg-subtle">
                <div className="h-px flex-1 bg-border" />
                или
                <div className="h-px flex-1 bg-border" />
              </div>
            </div>
          )}

          <form onSubmit={submit} className="mt-6 space-y-3">
            <Field icon={User} label="Логин">
              <input
                className="input pl-9"
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

            <Field icon={Lock} label="Пароль">
              <input
                className="input pl-9 pr-9"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                onClick={() => setShowPwd((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-fg-subtle hover:text-fg"
                aria-label="Показать"
              >
                {showPwd ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </Field>

            {err && (
              <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
                {err}
              </div>
            )}

            <button
              type="submit"
              disabled={!canSubmit}
              className="btn-primary w-full"
            >
              {busy ? <Spinner /> : <ShieldCheck className="h-3.5 w-3.5" />}
              Войти
            </button>
          </form>

          <div className="mt-6 rounded-xl border border-border bg-bg-subtle/50 p-3">
            <div className="flex items-start gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                <Bot className="h-4 w-4" />
              </div>
              <div className="text-xs text-fg-muted">
                Забыл пароль? Открой бота, напиши{" "}
                <code className="rounded bg-bg-elevated px-1 py-0.5 font-mono">/admin</code>{" "}
                и нажми <b>«Сбросить пароль»</b>. Дальше открой
                magic-ссылку и придумай новый.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof User;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div className="relative">
        <Icon className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
        {children}
      </div>
    </label>
  );
}
