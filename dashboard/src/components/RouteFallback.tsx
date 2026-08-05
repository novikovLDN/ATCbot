import { useLoadStage, Spinner } from "./ui/Loading";

/**
 * Заглушка на время загрузки чанка раздела.
 *
 * Маршруты собираются в отдельные файлы и подтягиваются при первом заходе.
 * До секунды не показываем ничего: чанк почти всегда приходит быстрее, а
 * мигание крутилкой раздражает сильнее самой задержки — это лестница задержек
 * из ux-patterns §3.1, она уже реализована в useLoadStage.
 */
export function RouteFallback() {
  const stage = useLoadStage(true);
  if (stage === "idle" || stage === "quiet") return null;
  return (
    <div className="grid min-h-[40vh] place-items-center">
      <Spinner label="Открываю раздел…" />
    </div>
  );
}
