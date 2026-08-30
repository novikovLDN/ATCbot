# dashboard — CLAUDE.md

Админ-панель ATCbot: **отдельное React+TS SPA (Vite)**, НЕ встроено в Python. Собирается отдельным
stage в корневом `Dockerfile` (`node:20-alpine` → `npm run build`), готовый `dashboard/dist` монтируется
FastAPI через `StaticFiles` (`app/api/__init__.py`).

## Стек

- React 18 + `react-router-dom`, `@tanstack/react-query` (серверный стейт), `zustand` (клиентский стор,
  `src/store/`), `recharts` (графики), `lucide-react` (иконки), `clsx` + `tailwind-merge`, **Tailwind CSS**.
- **Аутентификация — WebAuthn** (`@simplewebauthn/browser`): passkey/passwordless вход админа, НЕ логин/пароль.
- Структура: `src/{components,pages,lib,store}/`.

## Бэкенд дашборда

`app/api/dashboard/` (подмножество `app/api/`). Живая карта фичи с эндпоинтами/потоками —
`docs/admin_dashboard_implementation_map.md` (обновлять её при изменении API дашборда).

## Работа

- Изменения фронта — только в `dashboard/`; Python-часть не трогать без нужды.
- Токены/стиль — Tailwind + `clsx`/`tailwind-merge` (паттерн `cn()`), не инлайн-CSS.
- Данные — через `@tanstack/react-query` (кеш/инвалидация), не голый `fetch` в компонентах.
- Прод-сборка = `npm run build` внутри `dashboard/` (Docker делает это сам); дев — `npm run dev` (Vite).
