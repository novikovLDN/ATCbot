import { useState } from "react";
import { ShieldCheck, Bot, Lock, User, Eye, EyeOff } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { Spinner } from "@/components/Spinner";

export function SetupPassword({ bootstrapToken, onDone }: {
  bootstrapToken: string;
  onDone: () => void;
}) {
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
      await endpoints.authSetup({
        username: username.trim(),
        password,
        bootstrap_token: bootstrapToken,
      });
      onDone();
    } catch (e: unknown) {
      const ae = e as ApiError;
      if (ae?.status === 409) {
        setErr("Пароль уже установлен. Сбрось его через /admin → «Сбросить пароль» в боте.");
      } else if (ae?.status === 401) {
        setErr("Ссылка недействительна. Жми /admin в боте заново.");
      } else {
        setErr(ae?.detail ?? "Не удалось сохранить");
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
          <div className="mb-6 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-secondary text-white shadow-glow">
            <ShieldCheck className="h-5 w-5" strokeWidth={2.5} />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-fg">
            Первая настройка
          </h1>
          <p className="mt-2 text-sm text-fg-muted">
            Придумай <b>логин</b> и <b>пароль</b>. После сохранения этим
            логином/паролем будут открываться все будущие визиты — даже на
            этом же устройстве через 5 дней, когда сессия истечёт.
          </p>

          <form onSubmit={submit} className="mt-6 space-y-3">
            <Field icon={User} label="Логин">
              <input
                className="input pl-9"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="например, atlas"
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="username"
                spellCheck={false}
                required
                minLength={3}
                maxLength={40}
              />
            </Field>
            {username && !usernameOk && (
              <Hint kind="error">
                3-40 символов, латиница / цифры / <code>._-</code>
              </Hint>
            )}

            <Field icon={Lock} label="Пароль (мин. 8)">
              <input
                className="input pl-9 pr-9"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={200}
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

            <Field icon={Lock} label="Подтверди пароль">
              <input
                className="input pl-9"
                type={showPwd ? "text" : "password"}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                required
              />
            </Field>
            {confirm && !confirmOk && (
              <Hint kind="error">Пароли не совпадают</Hint>
            )}

            {err && <Hint kind="error">{err}</Hint>}

            <button
              type="submit"
              disabled={!canSubmit}
              className="btn-primary w-full"
            >
              {busy ? <Spinner /> : <ShieldCheck className="h-3.5 w-3.5" />}
              Создать аккаунт и войти
            </button>
          </form>

          <div className="mt-6 rounded-xl border border-border bg-bg-subtle/50 p-3">
            <div className="flex items-start gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                <Bot className="h-4 w-4" />
              </div>
              <div className="text-xs text-fg-muted">
                Magic-ссылка из /admin перестанет автоматически впускать
                в дашборд после этой настройки — даже у тебя. С этого
                момента: логин + пароль.
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

function Hint({
  kind,
  children,
}: {
  kind: "error" | "info";
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        kind === "error"
          ? "rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
          : "rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-xs text-fg-muted"
      }
    >
      {children}
    </div>
  );
}
