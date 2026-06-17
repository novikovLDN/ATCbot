import { useEffect, useState } from "react";
import { ShieldCheck, Bot, Lock, User, Eye, EyeOff, Fingerprint } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { isPasskeySupported, loginWithPasskey } from "@/lib/passkey";
import { Spinner } from "@/components/Spinner";

// 1.1s — длительность всей success-анимации (ring-pulse + check + lift-out),
// после которой переходим в дашборд. Совпадает с длительностями в
// tailwind.config.js → animation keys.
const SUCCESS_ANIM_MS = 1150;

export function Login({ onDone }: { onDone: () => void }) {
  const [passkeyAvailable, setPasskeyAvailable] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  // 'idle' → форма видна, 'success' → success-оверлей, после анимации onDone().
  const [phase, setPhase] = useState<"idle" | "success">("idle");
  const [welcomeName, setWelcomeName] = useState<string>("");

  const triggerSuccess = (name: string) => {
    setWelcomeName(name);
    setPhase("success");
    window.setTimeout(() => onDone(), SUCCESS_ANIM_MS);
  };

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
      triggerSuccess("admin");
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
      triggerSuccess(username.trim());
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
      {phase === "success" && (
        <SuccessOverlay name={welcomeName} />
      )}
      <div
        className={
          "card relative w-full max-w-md overflow-hidden p-8 animate-slide-up " +
          (phase === "success" ? "animate-lift-out pointer-events-none" : "")
        }
      >
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

// SuccessOverlay — успешная аутентификация:
// 1) расходящееся зелёное кольцо (ring-pulse, 1.1s)
// 2) checkmark рисуется stroke-dasharray поверх (check-draw, 0.45s,
//    стартует через 0.15s — синхронно с пиком кольца)
// 3) welcome-text fade-in
// 4) форма за оверлеем уходит вверх (lift-out)
// Всё уложено в 1.15s — после чего Login.onDone() триггерит redirect.
function SuccessOverlay({ name }: { name: string }) {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-bg-card/80 backdrop-blur-md animate-fade-in">
      <div className="flex flex-col items-center gap-5">
        {/* concentric rings + center disk */}
        <div className="relative grid h-24 w-24 place-items-center">
          {/* outer ring pulse */}
          <span className="absolute inset-0 rounded-full ring-2 ring-success/40 animate-ring-pulse" />
          {/* inner solid disk */}
          <span className="relative grid h-16 w-16 place-items-center rounded-full bg-success text-bg shadow-[0_0_30px_-2px_rgba(34,197,94,0.6)]">
            <svg
              viewBox="0 0 24 24"
              className="h-7 w-7"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path
                d="M5 12l4.5 4.5L19 7"
                style={{
                  strokeDasharray: 24,
                  strokeDashoffset: 24,
                  animation:
                    "check-draw 0.45s cubic-bezier(0.65, 0, 0.35, 1) forwards 0.15s",
                }}
              />
            </svg>
          </span>
        </div>
        <div className="text-center animate-fade-in" style={{ animationDelay: "0.3s", animationFillMode: "both" }}>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-muted">
            Authenticated
          </div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-fg">
            Welcome back{name ? `, ${name}` : ""}
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
