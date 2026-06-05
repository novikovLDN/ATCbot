import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth, captureMagicLink } from "@/lib/auth";
import { endpoints, ApiError } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { Toaster } from "@/components/Toaster";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { Users } from "@/pages/Users";
import { Analytics } from "@/pages/Analytics";
import { Audit } from "@/pages/Audit";
import { Broadcasts } from "@/pages/Broadcasts";
import { BroadcastCreate } from "@/pages/BroadcastCreate";
import { Referrals } from "@/pages/Referrals";
import { BypassGifts } from "@/pages/BypassGifts";

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

export default function App() {
  // Capture ?login=<jwt> exactly once before the rest of the tree mounts.
  // Doing it inside main.tsx before render also works; we duplicate here
  // for safety on hot-reload during dev.
  useEffect(() => {
    captureMagicLink();
  }, []);

  const [ready, setReady] = useState<null | boolean>(null);

  useEffect(() => {
    const t = auth.get();
    if (!t) {
      setReady(false);
      return;
    }
    // Single verify-call on app start. /auth/verify confirms signature
    // is valid and the bot's JWT_SECRET matches. On failure we clear
    // local storage and route to the login screen.
    endpoints
      .authVerify(t)
      .then(() => setReady(true))
      .catch(() => {
        auth.clear();
        setReady(false);
      });
  }, []);

  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter basename="/dashboard">
        {ready === null ? (
          <Splash />
        ) : ready ? (
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="users" element={<Users />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="broadcasts" element={<Broadcasts />} />
              <Route path="broadcasts/new" element={<BroadcastCreate />} />
              <Route path="referrals" element={<Referrals />} />
              <Route path="bgift" element={<BypassGifts />} />
              <Route path="audit" element={<Audit />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        ) : (
          <Routes>
            <Route path="*" element={<Login />} />
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
