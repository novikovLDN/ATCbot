import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { MobileNav } from "./MobileNav";
import { LiveIndicator } from "./LiveIndicator";
import { InstallHint } from "./InstallHint";

export function Layout() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex-1 overflow-y-auto pb-20 md:pb-0">
        <div
          className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-8"
          style={{
            paddingTop: "max(1.5rem, env(safe-area-inset-top))",
            paddingLeft: "max(1rem, env(safe-area-inset-left))",
            paddingRight: "max(1rem, env(safe-area-inset-right))",
          }}
        >
          <Outlet />
        </div>
      </main>
      <MobileNav />
      <LiveIndicator />
      <InstallHint />
    </div>
  );
}
