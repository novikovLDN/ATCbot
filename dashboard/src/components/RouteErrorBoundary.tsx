/**
 * Catches a render error inside one screen so the rest of the app (the
 * header, navigation, sign-out) keeps working. Without it a single
 * unexpected payload blanked the whole page — on an installed iOS app
 * there is no browser chrome, so the admin was left with nothing to tap.
 * RouteTransition keys it by pathname, so navigating away resets it.
 */
import { Component, type ReactNode } from "react";

export class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="tile flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex min-w-0 items-start gap-3">
          <span className="dot dot-err mt-1.5" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-[14px] font-medium">Экран не отрисовался</p>
            <p className="t-mute mt-1 break-words text-[13px]">{this.state.error.message}</p>
          </div>
        </div>
        <button type="button" className="btn-secondary min-h-[44px]" onClick={() => window.location.reload()}>
          Обновить
        </button>
      </div>
    );
  }
}
