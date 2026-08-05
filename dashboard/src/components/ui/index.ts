/**
 * Примитивы дизайн-системы. Новый код берёт компоненты отсюда.
 *
 * Что здесь есть и на чём основано (docs/dashboard-redesign):
 *   Button, Input          — токены §10.1–10.4, цели ≥24px (WCAG 2.2 SC 2.5.8)
 *   Table                  — плотность §10.5, липкая шапка, стрелки по строкам
 *   Card, StatTile         — карточка без тени и градиента (§8.3, §10.4)
 *   StatusBadge, StatusDot — шесть состояний, цвет + иконка + слово (§4.11)
 *   Modal, ConfirmDialog   — ловушка фокуса; подтверждение по правилам §2.1–2.3
 *   UndoBanner             — отмена в неисчезающем баннере, не в тосте (§2.4)
 *   Skeleton*              — только таблицы, карточки, плитки (§3.3)
 *   Loading*               — лестница задержек 1/2/10 секунд (§3.1)
 *   Empty*                 — четыре разных пустых состояния (§3.4)
 */
export { Button, type ButtonProps } from "./Button";
export { Input, type InputProps } from "./Input";
export { Card, CardHeader, CardBody, CardFooter, StatTile } from "./Card";
export { StatusBadge, StatusDot, type StatusKind } from "./StatusBadge";
export {
  Table,
  TableScroll,
  THead,
  TBody,
  TH,
  TD,
  TR,
  DensityToggle,
  Dash,
  type Density,
} from "./Table";
export { Modal } from "./Modal";
export { ConfirmDialog } from "./ConfirmDialog";
export { UndoBanner } from "./UndoBanner";
export { Skeleton, SkeletonTable, SkeletonTile, SkeletonCard } from "./Skeleton";
export {
  LoadingGate,
  Spinner,
  ProgressBar,
  useLoadStage,
  type LoadStage,
} from "./Loading";
export { EmptyFirstRun, EmptyFilter, EmptyNoAccess, EmptyFailure } from "./EmptyState";
