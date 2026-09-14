import { lazy, useCallback, useEffect, useState, type ComponentType } from "react";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth, captureMagicLink } from "@/lib/auth";
import { endpoints, ApiError } from "@/lib/api";
import { useBranding } from "@/lib/branding";
import { Shell } from "@/components/Shell";
import { Toaster } from "@/components/Toaster";
import { Login } from "@/pages/Login";
import { SetupPassword } from "@/pages/SetupPassword";

/**
 * Route-level code splitting: every screen is its own chunk, loaded on
 * first visit (RouteTransition wraps the outlet in Suspense). The entry
 * chunk keeps only the auth gate, the shell and the shared libraries,
 * so a phone opening the dashboard does not download the broadcast
 * editor or recharts before it can show the login screen.
 */
function page<K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) {
  return lazy(() => load().then((m) => ({ default: m[name] })));
}
const Overview = page(() => import("@/pages/Overview"), "Overview");
const Money = page(() => import("@/pages/Money"), "Money");
const Subscribers = page(() => import("@/pages/Subscribers"), "Subscribers");
const Panel = page(() => import("@/pages/Panel"), "Panel");
const Health = page(() => import("@/pages/Health"), "Health");
const Engagement = page(() => import("@/pages/Engagement"), "Engagement");
const Users = page(() => import("@/pages/Users"), "Users");
const Audit = page(() => import("@/pages/Audit"), "Audit");
const Broadcasts = page(() => import("@/pages/Broadcasts"), "Broadcasts");
const BroadcastCreate = page(() => import("@/pages/BroadcastCreate"), "BroadcastCreate");
const Referrals = page(() => import("@/pages/Referrals"), "Referrals");
const BypassGifts = page(() => import("@/pages/BypassGifts"), "BypassGifts");
const BetaApplications = page(() => import("@/pages/BetaApplications"), "BetaApplications");
const BypassAudit = page(() => import("@/pages/BypassAudit"), "BypassAudit");
const TrafficAudit = page(() => import("@/pages/TrafficAudit"), "TrafficAudit");
const PromoCodes = page(() => import("@/pages/PromoCodes"), "PromoCodes");
const Service = page(() => import("@/pages/Service"), "Service");
const SettingsScreen = page(() => import("@/pages/SettingsScreen"), "SettingsScreen");
const MarketingLinks = page(() => import("@/pages/MarketingLinks"), "MarketingLinks");
const AutomatedNotifications = page(() => import("@/pages/AutomatedNotifications"), "AutomatedNotifications");
const Statistics = page(() => import("@/pages/Statistics"), "Statistics");
const Pricing = page(() => import("@/pages/Pricing"), "Pricing");

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, err) => {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) return false;
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

function Gate() {
  useBranding();
  const [stage, setStage] = useState<Stage>({ kind: "loading" });

  const refresh = useCallback(async () => {
    try {
      const status = await endpoints.authStatus();
      if (status.has_session) {
        setStage({ kind: "ready" });
        return;
      }
      // First run (or right after "Сбросить пароль" in the bot): the
      // magic link's token lets the admin set a password. Otherwise the
      // link only opens the login screen.
      const token = auth.get();
      if (!status.has_password && !status.has_passkey && token) {
        setStage({ kind: "setup", bootstrapToken: token });
        return;
      }
      setStage({ kind: "login" });
    } catch {
      setStage({ kind: "login" });
    }
  }, []);

  useEffect(() => {
    captureMagicLink();
    refresh();
  }, [refresh]);

  if (stage.kind === "loading") return <Splash />;
  if (stage.kind === "setup")
    return (
      <SetupPassword
        bootstrapToken={stage.bootstrapToken}
        onDone={() => {
          auth.clear();
          refresh();
        }}
      />
    );
  if (stage.kind === "login")
    return (
      <Login
        onDone={() => {
          auth.clear();
          refresh();
        }}
      />
    );

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Overview />} />
        <Route path="money" element={<Money />} />
        <Route path="subscribers" element={<Subscribers />} />
        <Route path="panel" element={<Panel />} />
        <Route path="health" element={<Health />} />
        <Route path="engagement" element={<Engagement />} />
        {/* Operations became part of Health (v4); old links keep working. */}
        <Route path="operations" element={<Navigate to="/health" replace />} />
        <Route path="payments" element={<Navigate to="/money" replace />} />
        <Route path="users" element={<Users />} />
        {/* Replaced by v3 screens; old links keep working. */}
        <Route path="legacy" element={<Navigate to="/" replace />} />
        <Route path="analytics" element={<Navigate to="/money" replace />} />
        <Route path="statistics" element={<Statistics />} />
        <Route path="payments/legacy" element={<Navigate to="/health" replace />} />
        <Route path="pricing" element={<Pricing />} />
        <Route path="broadcasts" element={<Broadcasts />} />
        <Route path="broadcasts/new" element={<BroadcastCreate />} />
        <Route path="automated-notifications" element={<AutomatedNotifications />} />
        <Route path="referrals" element={<Referrals />} />
        <Route path="bgift" element={<BypassGifts />} />
        <Route path="beta-applications" element={<BetaApplications />} />
        <Route path="bypass-audit" element={<BypassAudit />} />
        <Route path="traffic-audit" element={<TrafficAudit />} />
        <Route path="audit" element={<Audit />} />
        <Route path="promo" element={<PromoCodes />} />
        <Route path="links" element={<MarketingLinks />} />
        <Route path="service" element={<Service />} />
        <Route path="settings" element={<SettingsScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter basename="/dashboard">
        <Gate />
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function Splash() {
  return (
    <div className="grid min-h-[100svh] place-items-center">
      <div className="capsule-nav px-4 py-2 text-[13px] text-mute" role="status">
        <span className="dot dot-accent animate-pulse-live" aria-hidden="true" />
        <span className="ml-2">Подключаюсь…</span>
      </div>
    </div>
  );
}
