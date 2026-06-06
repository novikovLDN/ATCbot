import { useEffect, useRef } from "react";
import { auth } from "./auth";

// Single flexible shape. Discriminated unions don't combine well with a
// catch-all branch (TS can't narrow via .startsWith()); instead we
// expose every known field as optional and let the handler verify the
// pieces it cares about. Runtime sources of truth are the bus.publish
// calls in app/api/dashboard/routes/users.py and database/*.
export type BusEvent = {
  type: string;
  telegram_id?: number;
  username?: string;
  payment_id?: number;
  is_renewal?: boolean;
  expires_at?: string;
  by?: number;
  days?: number;
  tariff?: string;
  percent?: number;
  minutes?: number;
  [k: string]: unknown;
};

type Handler = (e: BusEvent) => void;

/**
 * useEventStream — opens a WebSocket to /dashboard/ws and calls the
 * provided handler for every event. Reconnects with backoff. The
 * handler is referenced via a ref so callers don't need to memoize.
 */
export function useEventStream(handler: Handler, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;
    // Auth: the WS endpoint accepts either the session cookie (which
    // the browser sends automatically on same-origin connections) or
    // a JWT in the query string. Cookie is the new default after
    // password login; we keep ?token=… as a fallback when a magic-
    // link bootstrap token is still in localStorage.
    const token = auth.get();

    let ws: WebSocket | null = null;
    let closedByUs = false;
    let reconnectTimer: number | null = null;
    let attempt = 0;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = token
      ? `${protocol}//${window.location.host}/dashboard/ws?token=${encodeURIComponent(token)}`
      : `${protocol}//${window.location.host}/dashboard/ws`;

    const connect = () => {
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt = 0;
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as BusEvent;
          handlerRef.current(data);
        } catch {
          //
        }
      };
      ws.onclose = () => {
        if (closedByUs) return;
        attempt += 1;
        const delay = Math.min(2000 * attempt, 15000);
        reconnectTimer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          //
        }
      };
    };
    connect();

    return () => {
      closedByUs = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        //
      }
    };
  }, [enabled]);
}
