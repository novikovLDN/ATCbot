import { ShieldCheck, Bot, ArrowRight } from "lucide-react";

export function Login() {
  return (
    <div className="grid min-h-full place-items-center px-6 py-12">
      <div className="card relative w-full max-w-md overflow-hidden p-8 animate-slide-up">
        <div className="pointer-events-none absolute -top-32 left-1/2 h-64 w-64 -translate-x-1/2 rounded-full bg-accent/15 blur-3xl" />

        <div className="relative">
          <div className="mb-6 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-violet-500 text-white shadow-[0_8px_24px_-6px_rgba(99,102,241,0.5)]">
            <ShieldCheck className="h-5 w-5" strokeWidth={2.5} />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-fg">
            Atlas Admin
          </h1>
          <p className="mt-2 text-sm text-fg-muted">
            Войти можно только через Telegram-бот. Откройте бота, отправьте
            команду <code className="rounded bg-bg-elevated px-1.5 py-0.5 text-xs font-mono">/admin</code>{" "}
            и нажмите кнопку «Открыть дашборд».
          </p>

          <div className="mt-6 rounded-xl border border-border bg-bg-subtle/50 p-4">
            <div className="flex items-start gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                <Bot className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1 text-xs">
                <div className="font-medium text-fg">Magic-ссылка действует 10 минут</div>
                <div className="mt-1 text-fg-muted">
                  Если истекла — отправь <span className="font-mono">/admin</span> снова.
                  Никаких паролей, никаких форм входа.
                </div>
              </div>
            </div>
          </div>

          <div className="mt-8 flex items-center gap-2 text-xs text-fg-subtle">
            <span>Phase 1B • {new Date().getFullYear()}</span>
            <span className="text-fg-subtle/40">•</span>
            <span className="inline-flex items-center gap-1">
              Веб-UI <ArrowRight className="h-3 w-3" /> aiogram + FastAPI
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
