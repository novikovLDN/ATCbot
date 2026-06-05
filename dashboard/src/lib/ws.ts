import { useEffect, useRef } from "react";
import { auth } from "./auth";

export type BusEvent =
  | { type: "ping" }
  | { type: "user:registered"; telegram_id: number; username?: string }
  | { type: "payment:approved"; payment_id: number; telegram_id: number; is_renewal: boolean; expires_at: string }
  | { type: "admin:grant"; telegram_id: number; by: number; days: number; tariff: string }
  | { type: "admin:revoke"; telegram_id: number; by: number }
  | { type: "admin:discount_create"; telegram_id: number; percent: number; by: number }
  | { type: "admin:discount_delete"; telegram_id: number; by: number }
  | { type: "admin:vip_grant"; telegram_id: number; by: number }
  | { type: "admin:vip_revoke"; telegram_id: number; by: number }
  | { type: string; [k: string]: unknown };

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
    const token = auth.get();
    if (!token) return;

    let ws: WebSocket | null = null;
    let closedByUs = false;
    let reconnectTimer: number | null = null;
    let attempt = 0;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/dashboard/ws?token=${encodeURIComponent(token)}`;

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
