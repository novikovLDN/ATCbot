import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth, captureMagicLink } from "@/lib/auth";
import { endpoints, ApiError } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { Toaster } from "@/components/Toaster";
import { Login } from "@/pages/Login";
import { SetupPassword } from "@/pages/SetupPassword";
import { Dashboard } from "@/pages/Dashboard";
import { Users } from "@/pages/Users";
import { Analytics } from "@/pages/Analytics";
import { Audit } from "@/pages/Audit";
import { Broadcasts } from "@/pages/Broadcasts";
import { BroadcastCreate } from "@/pages/BroadcastCreate";
import { Referrals } from "@/pages/Referrals";
import { BypassGifts } from "@/pages/BypassGifts";
import { BypassAudit } from "@/pages/BypassAudit";
import { PromoCodes } from "@/pages/PromoCodes";
import { Service } from "@/pages/Service";
import { Payments } from "@/pages/Payments";
import { Settings } from "@/pages/Settings";
import { MarketingLinks } from "@/pages/MarketingLinks";
import { AutomatedNotifications } from "@/pages/AutomatedNotifications";
import { Statistics } from "@/pages/Statistics";
import { Pricing } from "@/pages/Pricing";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, err) => {
        if (err instanceof ApiError && err.status === 401) return false;
        return failureCount < 2;
      },
    },
  },
});

type Stage =
  | { kind: "loading" }
  | { kind: "setup"; bootstrapToken: string }
  | { kind: "login" }
  | { kind: "ready" };

export default function App() {
  useEffect(() => {
    captureMagicLink();
  }, []);

  const [stage, setStage] = useState<Stage>({ kind: "loading" });

  // Возвращает true, если сервер подтвердил живую сессию. Вызывающим это
  // нужно, чтобы не выбрасывать bootstrap-токен раньше времени — см.
  // onDone у SetupPassword ниже.
  const refresh = useCallback(async (): Promise<boolean> => {
    try {
      const status = await endpoints.authStatus();
      if (status.has_session) {
        setStage({ kind: "ready" });
        return true;
      }
      if (!status.has_password) {
        // Bootstrap setup needs a magic-link JWT
        const token = auth.get();
        if (!token) {
          setStage({ kind: "login" }); // no token, no setup — bot must issue link
          return false;
        }
        setStage({ kind: "setup", bootstrapToken: token });
        return false;
      }
      // Password exists; bearer JWT (if any) is no longer auto-login.
      setStage({ kind: "login" });
      return false;
    } catch {
      setStage({ kind: "login" });
      return false;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter basename="/dashboard">
        {stage.kind === "loading" ? (
          <Splash />
        ) : stage.kind === "setup" ? (
          <SetupPassword
            bootstrapToken={stage.bootstrapToken}
            onDone={async () => {
              // Токен из magic-ссылки выбрасываем ТОЛЬКО после того, как
              // /auth/status подтвердил сессию.
              //
              // Setup одноразовый, и это осознанно: ссылка живёт в
              // переписке с ботом вечно, второй setup по ней = смена
              // пароля без знания старого. Обратная сторона — цена
              // ошибки. Если set_credentials прошёл, а кука не встала
              // (оборвалась сеть на ответе, Safari выкинул её в
              // standalone-контексте), то очищенный токен означает: сессии
              // нет, второй setup даёт 409, и войти нечем — только сброс
              // пароля через бота, который заодно сносит все passkey.
              //
              // Пока сессии нет, токен остаётся в localStorage: он ещё
              // работает как Bearer для API (app/api/dashboard/deps.py) и
              // как ?token= для WebSocket.
              const ok = await refresh();
              if (ok) auth.clear();
            }}
          />
        ) : stage.kind === "login" ? (
          <Login
            onDone={() => {
              auth.clear();
              refresh();
            }}
          />
        ) : (
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="users" element={<Users />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="statistics" element={<Statistics />} />
              <Route path="pricing" element={<Pricing />} />
              <Route path="payments" element={<Payments />} />
              <Route path="broadcasts" element={<Broadcasts />} />
              <Route path="broadcasts/new" element={<BroadcastCreate />} />
              <Route path="automated-notifications" element={<AutomatedNotifications />} />
              <Route path="referrals" element={<Referrals />} />
              <Route path="bgift" element={<BypassGifts />} />
              <Route path="bypass-audit" element={<BypassAudit />} />
              <Route path="audit" element={<Audit />} />
              <Route path="promo" element={<PromoCodes />} />
              <Route path="links" element={<MarketingLinks />} />
              <Route path="service" element={<Service />} />
              <Route path="settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        )}
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function Splash() {
  return (
    <div className="grid h-full place-items-center">
      <div className="card flex items-center gap-3 px-4 py-3 text-sm text-fg-muted">
        <span className="h-2 w-2 animate-pulse-glow rounded-full bg-accent" />
        Подключаюсь...
      </div>
    </div>
  );
}
