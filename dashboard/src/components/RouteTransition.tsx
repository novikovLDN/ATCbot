import { useLocation, Outlet } from "react-router-dom";
import { useEffect, useRef, useState } from "react";

/**
 * Плавный fade+slide-in контента при смене роута. На каждый новый
 * pathname мы меняем ключ у div'а — React монтирует его заново, CSS-
 * анимация route-in выполняется на mount. Без framer-motion: чистые
 * CSS keyframes из tailwind.config.js.
 */
export function RouteTransition() {
  const loc = useLocation();
  const [key, setKey] = useState(loc.pathname);
  const previousRef = useRef(loc.pathname);

  useEffect(() => {
    if (previousRef.current !== loc.pathname) {
      previousRef.current = loc.pathname;
      setKey(loc.pathname);
    }
  }, [loc.pathname]);

  return (
    <div key={key} className="route-in">
      <Outlet />
    </div>
  );
}
