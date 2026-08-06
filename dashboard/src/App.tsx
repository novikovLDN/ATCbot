import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "@/lib/auth";
import { endpoints, ApiError } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { SectionTabs } from "@/components/SectionTabs";
import { Toaster } from "@/components/Toaster";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Login } from "@/pages/Login";
import { SetupPassword } from "@/pages/SetupPassword";

// ─ Экраны грузятся отдельными чанками ────────────────────────────────
//
// Раньше всё приложение собиралось одним файлом на мегабайт: заходя на
// «Настройки», человек скачивал заодно графики главной, редактор рассылок и
// оба журнала. Теперь каждый маршрут — свой чанк, и recharts (самая тяжёлая
// зависимость) приезжает только на те экраны, где есть графики.
//
// Login и SetupPassword остаются в основном чанке сознательно: это первое,
// что видит неавторизованный, и грузить их вторым запросом — задержка ровно
// там, где её видно.
const Dashboard = lazy(() => import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Users = lazy(() => import("@/pages/Users").then((m) => ({ default: m.Users })));
const Payments = lazy(() => import("@/pages/Payments").then((m) => ({ default: m.Payments })));
const Events = lazy(() => import("@/pages/Events").then((m) => ({ default: m.Events })));
const EventsBypass = lazy(() =>
  import("@/pages/EventsBypass").then((m) => ({ default: m.EventsBypass })),
);
const Analytics = lazy(() => import("@/pages/Analytics").then((m) => ({ default: m.Analytics })));
const Statistics = lazy(() =>
  import("@/pages/Statistics").then((m) => ({ default: m.Statistics })),
);
const Broadcasts = lazy(() =>
  import("@/pages/Broadcasts").then((m) => ({ default: m.Broadcasts })),
);
const BroadcastCreate = lazy(() =>
  import("@/pages/BroadcastCreate").then((m) => ({ default: m.BroadcastCreate })),
);
const AutomatedNotifications = lazy(() =>
  import("@/pages/AutomatedNotifications").then((m) => ({ default: m.AutomatedNotifications })),
);
const Pricing = lazy(() => import("@/pages/Pricing").then((m) => ({ default: m.Pricing })));
const PromoCodes = lazy(() =>
  import("@/pages/PromoCodes").then((m) => ({ default: m.PromoCodes })),
);
const MarketingLinks = lazy(() =>
  import("@/pages/MarketingLinks").then((m) => ({ default: m.MarketingLinks })),
);
const Referrals = lazy(() => import("@/pages/Referrals").then((m) => ({ default: m.Referrals })));
const BypassGifts = lazy(() =>
  import("@/pages/BypassGifts").then((m) => ({ default: m.BypassGifts })),
);
const Service = lazy(() => import("@/pages/Service").then((m) => ({ default: m.Service })));
const Settings = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })));

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

// Витрина примитивов. Грузится динамически и только в dev: в продовой сборке
// import.meta.env.DEV — константа false, ветка ниже недостижима, и страница в
// бандл не попадает. Открывается по /dashboard/ui-kit мимо авторизации —
// удобно смотреть компоненты, когда бэкенд не поднят.
const UiKit = import.meta.env.DEV ? lazy(() => import("@/pages/UiKit")) : null;

// Проверка вынесена из компонента: раньше она стояла между хуками и делала
// количество вызовов хуков зависимым от адреса — ровно то нарушение правил
// хуков, из-за которого в этом проекте уже дважды ловили белый экран.
const SHOW_UI_KIT =
  UiKit !== null && window.location.pathname.startsWith("/dashboard/ui-kit");

export default function App() {
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
    if (SHOW_UI_KIT) return;
    refresh();
  }, [refresh]);

  if (SHOW_UI_KIT && UiKit) {
    return (
      <Suspense fallback={null}>
        <UiKit />
      </Suspense>
    );
  }

  return (
    // Общая граница ошибок вокруг всего приложения. До неё любое исключение в
    // отрисовке давало белый экран без единого слова (аудит §1).
    <ErrorBoundary variant="page">
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
            <AppRoutes />
          )}
          <Toaster />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

/**
 * Маршруты.
 *
 * Разделов девять вместо шестнадцати (research §9.2), но ни один экран не
 * пропал: бывшие пункты меню стали вкладками внутри разделов, а все прежние
 * адреса ниже редиректят на новые места. Ссылка из старой переписки или
 * закладка браузера продолжает открывать то же самое.
 *
 *   /audit                    → /events
 *   /bypass-audit             → /events/bypass
 *   /statistics               → /analytics/stats
 *   /automated-notifications  → /broadcasts/auto
 *   /pricing                  → /monetization
 *   /promo                    → /monetization/promo
 *   /links                    → /monetization/links
 *   /referrals                → /monetization/referrals
 *   /bgift                    → /monetization/bgift
 *
 * /broadcasts/new остался прежним: на него ссылаются кнопки со сводки и со
 * списка рассылок, и менять адрес ради симметрии было бы плохой сделкой.
 */
function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="users" element={<Users />} />
        <Route path="payments" element={<Payments />} />

        {/* События = слияние двух журналов: «Аудит» и «Bypass Audit».
            Оба отвечали на вопрос «что произошло» и стояли в меню
            отдельными пунктами (research §4.7). */}
        <Route path="events" element={<SectionTabs />}>
          <Route index element={<Events />} />
          <Route path="bypass" element={<EventsBypass />} />
        </Route>

        <Route path="analytics" element={<SectionTabs />}>
          <Route index element={<Analytics />} />
          <Route path="stats" element={<Statistics />} />
        </Route>

        <Route path="broadcasts" element={<SectionTabs />}>
          <Route index element={<Broadcasts />} />
          <Route path="new" element={<BroadcastCreate />} />
          <Route path="auto" element={<AutomatedNotifications />} />
        </Route>

        <Route path="monetization" element={<SectionTabs />}>
          <Route index element={<Pricing />} />
          <Route path="promo" element={<PromoCodes />} />
          <Route path="links" element={<MarketingLinks />} />
          <Route path="referrals" element={<Referrals />} />
          <Route path="bgift" element={<BypassGifts />} />
        </Route>

        <Route path="service" element={<Service />} />
        <Route path="settings" element={<Settings />} />

        {/* Старые адреса. replace, чтобы «назад» не отбрасывал обратно на
            редирект и не зацикливал историю. */}
        <Route path="audit" element={<Navigate to="/events" replace />} />
        <Route path="bypass-audit" element={<Navigate to="/events/bypass" replace />} />
        <Route path="statistics" element={<Navigate to="/analytics/stats" replace />} />
        <Route
          path="automated-notifications"
          element={<Navigate to="/broadcasts/auto" replace />}
        />
        <Route path="pricing" element={<Navigate to="/monetization" replace />} />
        <Route path="promo" element={<Navigate to="/monetization/promo" replace />} />
        <Route path="links" element={<Navigate to="/monetization/links" replace />} />
        <Route path="referrals" element={<Navigate to="/monetization/referrals" replace />} />
        <Route path="bgift" element={<Navigate to="/monetization/bgift" replace />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function Splash() {
  return (
    <div className="grid h-full place-items-center">
      <div className="card flex items-center gap-3 px-4 py-3 text-base text-fg-muted">
        <span className="h-2 w-2 animate-pulse-live rounded-full bg-info-solid" />
        Подключаюсь...
      </div>
    </div>
  );
}
