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

// ─ Одно соединение на вкладку ────────────────────────────────────────
//
// Раньше useEventStream поднимал свой WebSocket в каждом вызывающем
// компоненте. На главной хуков два — лента платежей (LivePaymentTicker)
// и список событий (Dashboard), — значит два сокета, две очереди в шине
// на сервере (app/events.py), два ping-таймера и два независимых цикла
// переподключения. События в них при этом одинаковые: шина вещает всем.
//
// Держим один сокет на вкладку и раздаём события подписчикам.
//
// Почему модульная переменная, а не React-контекст: подписчики живут в
// разных ветках дерева (страницы, виджеты, Layout), провайдер пришлось
// бы поднимать в App и тащить пропсами через всё. Сокет — ресурс
// вкладки, а не узла дерева, и переживает перерисовки роутера.

const subscribers = new Set<Handler>();
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let idleCloseTimer: number | null = null;
let attempt = 0;

/** Пауза перед закрытием сокета, когда подписчиков не осталось.
 *
 * Нужна из-за двух штатных ситуаций, в которых счётчик проседает до
 * нуля на доли секунды: StrictMode в dev монтирует эффекты дважды, а
 * переход между страницами сначала размонтирует старую и только потом
 * монтирует новую. Рвать соединение ради этого — лишний handshake и
 * дыра, в которую проваливаются события. */
const IDLE_CLOSE_MS = 1000;

function wsUrl(): string {
  // Auth: the WS endpoint accepts either the session cookie (which
  // the browser sends automatically on same-origin connections) or
  // a JWT in the query string. Cookie is the new default after
  // password login; we keep ?token=… as a fallback when a magic-
  // link bootstrap token is still in localStorage.
  const token = auth.get();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = `${protocol}//${window.location.host}/dashboard/ws`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

function connect() {
  if (socket) return;
  const ws = new WebSocket(wsUrl());
  socket = ws;

  ws.onopen = () => {
    attempt = 0;
  };

  ws.onmessage = (ev) => {
    let data: BusEvent;
    try {
      data = JSON.parse(ev.data) as BusEvent;
    } catch {
      return;
    }
    // Копия набора: обработчик волен отписаться прямо в колбэке, и
    // мутация Set во время обхода иначе бы всё уронила. try на каждом —
    // один сломанный подписчик не должен глушить остальных.
    for (const h of Array.from(subscribers)) {
      try {
        h(data);
      } catch {
        //
      }
    }
  };

  ws.onclose = () => {
    // Если socket уже не наш — значит нас закрыли осознанно
    // (teardown) и переподключаться не надо.
    if (socket !== ws) return;
    socket = null;
    if (subscribers.size === 0) return;
    attempt += 1;
    const delay = Math.min(2000 * attempt, 15000);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  };

  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      //
    }
  };
}

function teardown() {
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  const ws = socket;
  socket = null;
  attempt = 0;
  if (ws) {
    try {
      ws.close();
    } catch {
      //
    }
  }
}

function subscribe(handler: Handler): () => void {
  subscribers.add(handler);
  if (idleCloseTimer !== null) {
    window.clearTimeout(idleCloseTimer);
    idleCloseTimer = null;
  }
  // Если переподключение уже запланировано — не лезем вперёд него.
  // Иначе при лежащем сервере каждый переход между страницами дёргал бы
  // новый handshake, обнуляя backoff.
  if (reconnectTimer === null) connect();

  return () => {
    subscribers.delete(handler);
    if (subscribers.size > 0) return;
    if (idleCloseTimer !== null) window.clearTimeout(idleCloseTimer);
    idleCloseTimer = window.setTimeout(() => {
      idleCloseTimer = null;
      if (subscribers.size === 0) teardown();
    }, IDLE_CLOSE_MS);
  };
}

/**
 * useEventStream — подписывает handler на общий поток событий дашборда.
 * Соединение одно на вкладку (см. комментарий выше), переподключение с
 * backoff'ом живёт в модуле. Handler держим в ref'е, чтобы вызывающему
 * не приходилось его мемоизировать: подписка стабильна на всё время
 * жизни компонента, меняется только её начинка.
 */
export function useEventStream(handler: Handler, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;
    return subscribe((e) => handlerRef.current(e));
  }, [enabled]);
}
