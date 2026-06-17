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

// 1.15s — длительность всей success-анимации (ring-pulse + check +
// lift-out), после которой переходим в дашборд. Совпадает с
// длительностями анимаций в tailwind.config.js.
const SUCCESS_ANIM_MS = 1150;

export function Login({ onDone }: { onDone: () => void }) {
  const [passkeyAvailable, setPasskeyAvailable] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  // 'idle' → форма видна, 'success' → success-оверлей, после анимации → onDone().
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
      triggerSuccess("Founder");
    } catch (e: unknown) {
      const ae = e as ApiError;
      if (ae?.detail === "cancelled") {
        // user dismissed OS prompt — silent
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
    <div className="relative grid min-h-full place-items-center overflow-hidden bg-[#070A14] px-6 py-12 text-white">
      {/* ─ Technologic animated background ─────────────────────────── */}
      <AuroraBackground />

      {phase === "success" && <SuccessOverlay name={welcomeName} />}

      {/* ─ Card ─────────────────────────────────────────────────────── */}
      <div
        className={
          "relative z-10 w-full max-w-md animate-mount-card " +
          (phase === "success" ? "animate-lift-out pointer-events-none" : "")
        }
      >
        {/* Soft conic glow за карточкой — медленно вращается */}
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-8 -z-10 opacity-70 animate-glow-rotate"
          style={{
            background:
              "conic-gradient(from 0deg, rgba(56,189,248,0.25), rgba(168,85,247,0.18), rgba(236,72,153,0.15), rgba(56,189,248,0.25))",
            filter: "blur(48px)",
          }}
        />

        {/* Glass-card */}
        <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.04] p-8 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
          {/* Top thin gradient line — «энергетическая полоса» */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-12 top-0 h-px"
            style={{
              background:
                "linear-gradient(90deg, transparent, rgba(56,189,248,0.7), rgba(168,85,247,0.55), transparent)",
            }}
          />

          {/* Logo + brand */}
          <div className="flex items-center gap-3">
            <div className="relative">
              <div
                aria-hidden
                className="absolute -inset-1 rounded-2xl opacity-60 blur-md"
                style={{
                  background:
                    "linear-gradient(135deg, #38BDF8, #A855F7)",
                }}
              />
              <div className="relative grid h-11 w-11 place-items-center rounded-2xl border border-white/20 bg-gradient-to-br from-sky-400 to-violet-500 text-white shadow-[0_4px_16px_-4px_rgba(56,189,248,0.55)]">
                <ShieldCheck className="h-[18px] w-[18px]" strokeWidth={2.5} />
              </div>
            </div>
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/40">
                Atlas Secure
              </div>
              <div className="mt-0.5 inline-flex items-center gap-1.5 text-sm font-semibold text-white">
                Founder Console
                <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
              </div>
            </div>
          </div>

          <div className="mt-8">
            <h1 className="text-[28px] font-semibold leading-tight tracking-tight text-white">
              Доступ только для своих.
            </h1>
            <p className="mt-2 text-sm text-white/50">
              Войди по логину или ключу устройства. Сессия — <b className="text-white/80">5 дней</b>.
            </p>
          </div>

          {passkeyAvailable && (
            <div className="mt-7">
              <button
                type="button"
                onClick={onPasskey}
                disabled={passkeyBusy}
                className="group relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl border border-white/15 bg-white/5 px-4 py-3 text-sm font-medium text-white transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:border-white/30 hover:bg-white/[0.08] hover:shadow-[0_10px_30px_-12px_rgba(56,189,248,0.5)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {/* sweep highlight on hover */}
                <span
                  aria-hidden
                  className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/10 to-transparent transition-transform duration-700 group-hover:translate-x-full"
                />
                {passkeyBusy ? (
                  <Spinner />
                ) : (
                  <Fingerprint className="h-4 w-4 text-sky-300" />
                )}
                Войти через Face ID / Touch ID
              </button>
              <div className="my-5 flex items-center gap-3 text-[10px] font-medium uppercase tracking-[0.2em] text-white/30">
                <div className="h-px flex-1 bg-white/10" />
                или
                <div className="h-px flex-1 bg-white/10" />
              </div>
            </div>
          )}

          <form onSubmit={submit} className="space-y-3">
            <DarkField icon={User} label="Логин">
              <input
                className="dark-input pl-9"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="username"
                spellCheck={false}
                required
                autoFocus
              />
            </DarkField>

            <DarkField icon={Lock} label="Пароль">
              <input
                className="dark-input pl-9 pr-9"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                onClick={() => setShowPwd((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-white/40 hover:text-white"
                aria-label="Показать"
              >
                {showPwd ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </DarkField>

            {err && (
              <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                {err}
              </div>
            )}

            <button
              type="submit"
              disabled={!canSubmit}
              className="group relative mt-2 inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-sky-400 to-violet-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_10px_30px_-10px_rgba(56,189,248,0.6)] transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:shadow-[0_18px_40px_-12px_rgba(168,85,247,0.6)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
            >
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/30 to-transparent transition-transform duration-700 group-hover:translate-x-full"
              />
              {busy ? <Spinner /> : <ArrowUpRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />}
              Войти
            </button>
          </form>

          <div className="mt-6 rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-start gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-white/10 bg-white/[0.05] text-white/60">
                <Bot className="h-4 w-4" />
              </div>
              <div className="text-xs text-white/55">
                Забыл пароль? Открой бота, напиши{" "}
                <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-white/80">
                  /admin
                </code>{" "}
                и нажми <b className="text-white">«Сбросить пароль»</b>.
                Дальше открой magic-ссылку и придумай новый.
              </div>
            </div>
          </div>
        </div>

        {/* Footer note под карточкой */}
        <div className="mt-6 flex items-center justify-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/30">
          <span className="h-1 w-1 rounded-full bg-white/30" />
          Encrypted · 256-bit · Atlas Secure ©
          <span className="h-1 w-1 rounded-full bg-white/30" />
        </div>
      </div>
    </div>
  );
}

// AuroraBackground — анимированный технологичный фон. Три слоя:
// 1) Чёрно-синий радиальный градиент (vignette);
// 2) Двигающиеся blobs в фиолетово-голубых тонах через CSS animation;
// 3) Тонкая mesh-сетка через SVG-pattern с pulse opacity.
function AuroraBackground() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {/* base radial vignette */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(56,189,248,0.15), transparent 60%), radial-gradient(ellipse 70% 50% at 50% 100%, rgba(168,85,247,0.15), transparent 60%), #070A14",
        }}
      />
      {/* mesh grid */}
      <svg
        className="absolute inset-0 h-full w-full opacity-[0.18]"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <pattern
            id="grid"
            width="48"
            height="48"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 48 0 L 0 0 0 48"
              fill="none"
              stroke="rgba(148,163,184,0.18)"
              strokeWidth="0.5"
            />
          </pattern>
          <radialGradient id="gridFade" cx="50%" cy="50%" r="60%">
            <stop offset="0%" stopColor="white" stopOpacity="1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </radialGradient>
          <mask id="m">
            <rect width="100%" height="100%" fill="url(#gridFade)" />
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" mask="url(#m)" />
      </svg>
      {/* floating blobs */}
      <div className="absolute left-[8%] top-[12%] h-72 w-72 rounded-full opacity-50 blur-3xl animate-blob-slow"
           style={{ background: "radial-gradient(circle, rgba(56,189,248,0.55), transparent 60%)" }} />
      <div className="absolute right-[12%] top-[40%] h-80 w-80 rounded-full opacity-45 blur-3xl animate-blob-slow-2"
           style={{ background: "radial-gradient(circle, rgba(168,85,247,0.5), transparent 60%)" }} />
      <div className="absolute left-[30%] bottom-[8%] h-72 w-72 rounded-full opacity-40 blur-3xl animate-blob-slow-3"
           style={{ background: "radial-gradient(circle, rgba(236,72,153,0.4), transparent 60%)" }} />
    </div>
  );
}

// SuccessOverlay — успешная аутентификация: ring-pulse + checkmark +
// welcome-text → 1.15s → onDone() в Login. Стиль адаптирован под
// тёмный фон Login.
function SuccessOverlay({ name }: { name: string }) {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-[#070A14]/85 backdrop-blur-md animate-fade-in">
      <div className="flex flex-col items-center gap-5">
        <div className="relative grid h-24 w-24 place-items-center">
          <span className="absolute inset-0 rounded-full ring-2 ring-emerald-400/40 animate-ring-pulse" />
          <span className="relative grid h-16 w-16 place-items-center rounded-full bg-emerald-500 text-white shadow-[0_0_30px_-2px_rgba(34,197,94,0.7)]">
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
        <div
          className="text-center animate-fade-in"
          style={{ animationDelay: "0.3s", animationFillMode: "both" }}
        >
          <div className="text-[10px] font-medium uppercase tracking-[0.22em] text-white/40">
            Authenticated
          </div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-white">
            Welcome back{name ? `, ${name}` : ""}
          </div>
        </div>
      </div>
    </div>
  );
}

// DarkField — input в тёмной теме Login. Не использует общий .input
// (тот для светлой темы дашборда). Тонкая обводка, glass-bg, focus
// — sky-glow.
function DarkField({
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
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-white/40">
        {label}
      </div>
      <div className="relative">
        <Icon className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
        {children}
      </div>
    </label>
  );
}
