import { useEffect, useState } from "react";
import {
  ShieldCheck,
  Bot,
  Lock,
  User,
  Eye,
  EyeOff,
  Fingerprint,
  ArrowUpRight,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { isPasskeySupported, loginWithPasskey } from "@/lib/passkey";
import { Spinner } from "@/components/Spinner";

const SUCCESS_ANIM_MS = 1150;

export function Login({ onDone }: { onDone: () => void }) {
  const [passkeyAvailable, setPasskeyAvailable] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
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
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  const onPasskey = async () => {
    setPasskeyBusy(true);
    try {
      await loginWithPasskey();
      triggerSuccess("Founder");
    } catch (e: unknown) {
      const ae = e as ApiError;
      if (ae?.detail === "cancelled") {
        //
      } else {
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
      triggerSuccess(username.trim() || "Founder");
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
    <div className="relative grid min-h-full place-items-center overflow-hidden bg-bg px-6 py-12 text-fg">
      <AuroraDarkBackground />

      {phase === "success" && <SuccessOverlay name={welcomeName} />}

      <div
        className={
          "relative z-10 w-full max-w-md animate-mount-card " +
          (phase === "success" ? "animate-lift-out pointer-events-none" : "")
        }
      >
        {/* Лаймовое halo за карточкой — медленное вращение */}
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-10 -z-10 opacity-50 animate-glow-rotate"
          style={{
            background:
              "conic-gradient(from 0deg, rgba(215,255,103,0.30), rgba(166,255,179,0.20), rgba(215,255,103,0.08), rgba(215,255,103,0.30))",
            filter: "blur(80px)",
          }}
        />

        <div className="relative overflow-hidden rounded-3xl border border-border bg-bg-card/85 p-8 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
          {/* Лаймовый штрих сверху */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-12 top-0 h-px"
            style={{
              background:
                "linear-gradient(90deg, transparent, rgba(215,255,103,0.7), rgba(215,255,103,0.3), transparent)",
            }}
          />

          {/* Logo + brand */}
          <div className="flex items-center gap-3">
            <div className="relative">
              <div
                aria-hidden
                className="absolute -inset-1 rounded-2xl opacity-60 blur-md bg-accent/60"
              />
              <div className="relative grid h-11 w-11 place-items-center rounded-2xl bg-accent text-bg shadow-glow">
                <ShieldCheck className="h-[18px] w-[18px]" strokeWidth={2.5} />
              </div>
            </div>
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-fg-subtle">
                Atlas Secure
              </div>
              <div className="mt-0.5 inline-flex items-center gap-1.5 text-sm font-semibold text-fg">
                Founder Console
                <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-accent shadow-[0_0_8px_rgba(215,255,103,0.7)]" />
              </div>
            </div>
          </div>

          <div className="mt-8">
            <h1 className="text-[28px] font-semibold leading-tight tracking-tight text-fg">
              Войди в свой кабинет.
            </h1>
            <p className="mt-2 text-sm text-fg-muted">
              По логину и паролю или через ключ устройства. Сессия — <b className="text-fg">5 дней</b>.
            </p>
          </div>

          {passkeyAvailable && (
            <div className="mt-7">
              <button
                type="button"
                onClick={onPasskey}
                disabled={passkeyBusy}
                className="group relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl border border-border bg-bg-elevated px-4 py-3 text-sm font-medium text-fg transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span
                  aria-hidden
                  className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-accent/15 to-transparent transition-transform duration-700 group-hover:translate-x-full"
                />
                {passkeyBusy ? (
                  <Spinner />
                ) : (
                  <Fingerprint className="h-4 w-4 text-accent" />
                )}
                Войти через Face ID / Touch ID
              </button>
              <div className="my-5 flex items-center gap-3 text-[10px] font-medium uppercase tracking-[0.2em] text-fg-subtle">
                <div className="h-px flex-1 bg-border" />
                или
                <div className="h-px flex-1 bg-border" />
              </div>
            </div>
          )}

          <form onSubmit={submit} className="space-y-3">
            <LightField icon={User} label="Логин">
              <input
                className="light-input pl-9"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="username"
                spellCheck={false}
                required
                autoFocus
              />
            </LightField>

            <LightField icon={Lock} label="Пароль">
              <input
                className="light-input pl-9 pr-9"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                onClick={() => setShowPwd((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-fg-subtle transition-colors hover:text-fg"
                aria-label="Показать"
              >
                {showPwd ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </LightField>

            {err && (
              <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
                {err}
              </div>
            )}

            <button
              type="submit"
              disabled={!canSubmit}
              className="group relative mt-2 inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-accent px-4 py-3 text-sm font-semibold text-bg shadow-glow transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:bg-accent-hover hover:shadow-[0_18px_40px_-12px_rgba(215,255,103,0.55)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
            >
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-bg/30 to-transparent transition-transform duration-700 group-hover:translate-x-full"
              />
              {busy ? (
                <Spinner />
              ) : (
                <ArrowUpRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              )}
              Войти
            </button>
          </form>

          <div className="mt-6 rounded-xl border border-border bg-bg-elevated/60 p-3">
            <div className="flex items-start gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-border bg-bg-card text-fg-muted">
                <Bot className="h-4 w-4" />
              </div>
              <div className="text-xs text-fg-muted">
                Забыл пароль? Открой бота, напиши{" "}
                <code className="rounded bg-bg px-1 py-0.5 font-mono text-fg">
                  /admin
                </code>{" "}
                и нажми <b className="text-fg">«Сбросить пароль»</b>.
                Дальше открой magic-ссылку и придумай новый.
              </div>
            </div>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-center gap-2 text-[10px] uppercase tracking-[0.18em] text-fg-subtle">
          <span className="h-1 w-1 rounded-full bg-fg-subtle/50" />
          Encrypted · 256-bit · Atlas Secure ©
          <span className="h-1 w-1 rounded-full bg-fg-subtle/50" />
        </div>
      </div>
    </div>
  );
}

// AuroraDarkBackground — тёмный технологичный фон под brand-deck.
// 1) Глубокий канвас + лаймовое halo сверху, мятный glow снизу
// 2) SVG mesh-сетка тонкая, тёмный stroke с radial-fade в центре
// 3) Лаймовые drift-blob'ы (slow drift, ощущение «технологичного nebula»)
function AuroraDarkBackground() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(215,255,103,0.16), transparent 60%), radial-gradient(ellipse 70% 50% at 50% 100%, rgba(166,255,179,0.10), transparent 60%), #0A0A0A",
        }}
      />
      <svg className="absolute inset-0 h-full w-full opacity-[0.25]" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="grid-dark" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke="#2A2A2A" strokeWidth="0.5" />
          </pattern>
          <radialGradient id="gridFadeDark" cx="50%" cy="50%" r="65%">
            <stop offset="0%" stopColor="white" stopOpacity="1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </radialGradient>
          <mask id="md">
            <rect width="100%" height="100%" fill="url(#gridFadeDark)" />
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid-dark)" mask="url(#md)" />
      </svg>
      <div
        className="absolute left-[8%] top-[12%] h-72 w-72 rounded-full blur-3xl opacity-40 animate-blob-slow"
        style={{ background: "radial-gradient(circle, rgba(215,255,103,0.45), transparent 60%)" }}
      />
      <div
        className="absolute right-[12%] top-[40%] h-80 w-80 rounded-full blur-3xl opacity-35 animate-blob-slow-2"
        style={{ background: "radial-gradient(circle, rgba(166,255,179,0.35), transparent 60%)" }}
      />
      <div
        className="absolute left-[30%] bottom-[8%] h-72 w-72 rounded-full blur-3xl opacity-25 animate-blob-slow-3"
        style={{ background: "radial-gradient(circle, rgba(215,255,103,0.30), transparent 60%)" }}
      />
    </div>
  );
}

function SuccessOverlay({ name }: { name: string }) {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-bg/80 backdrop-blur-md animate-fade-in">
      <div className="flex flex-col items-center gap-5">
        <div className="relative grid h-24 w-24 place-items-center">
          <span className="absolute inset-0 rounded-full ring-2 ring-accent/50 animate-ring-pulse" />
          <span className="relative grid h-16 w-16 place-items-center rounded-full bg-accent text-bg shadow-[0_0_30px_-2px_rgba(215,255,103,0.55)]">
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path
                d="M5 12l4.5 4.5L19 7"
                style={{
                  strokeDasharray: 24,
                  strokeDashoffset: 24,
                  animation: "check-draw 0.45s cubic-bezier(0.65, 0, 0.35, 1) forwards 0.15s",
                }}
              />
            </svg>
          </span>
        </div>
        <div className="text-center animate-fade-in" style={{ animationDelay: "0.3s", animationFillMode: "both" }}>
          <div className="text-[10px] font-medium uppercase tracking-[0.22em] text-fg-subtle">
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

function LightField({
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
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
        {label}
      </div>
      <div className="relative">
        <Icon className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
        {children}
      </div>
    </label>
  );
}
