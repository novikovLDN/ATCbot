import { useCallback, useEffect, useRef } from "react";

import { useEventStream } from "@/lib/ws";

/**
 * Толчок из WebSocket, притормозленный по времени.
 *
 * Шина шлёт событие на каждую регистрацию и каждый платёж; во время
 * рассылки это десятки в минуту. Дёргать запрос на каждое — положить
 * базу ровно тогда, когда она нужнее всего.
 *
 * Первое событие обновляет сразу, всё остальное за окно схлопывается в
 * один отложенный вызов — так последнее событие всплеска не теряется
 * (иначе после затишья экран остался бы с данными до всплеска).
 *
 * WebSocket здесь НЕ рисует строки, а только просит перезапросить. Две
 * ленты — серверная и накопленная в браузере — неизбежно разъезжаются, и
 * после перезагрузки страницы человек видит другой набор событий, чем
 * секунду назад. Та же политика у ленты на «Сводке».
 */
export function useLiveRefresh(refresh: () => void, windowMs = 5000) {
  const lastRef = useRef(0);
  const pendingRef = useRef<number | null>(null);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  const push = useCallback(() => {
    const wait = windowMs - (Date.now() - lastRef.current);
    if (wait <= 0) {
      lastRef.current = Date.now();
      refreshRef.current();
      return;
    }
    if (pendingRef.current !== null) return;
    pendingRef.current = window.setTimeout(() => {
      pendingRef.current = null;
      lastRef.current = Date.now();
      refreshRef.current();
    }, wait);
  }, [windowMs]);

  useEffect(
    () => () => {
      if (pendingRef.current !== null) window.clearTimeout(pendingRef.current);
    },
    [],
  );

  useEventStream((e) => {
    if (e.type === "ping") return;
    push();
  });
}
