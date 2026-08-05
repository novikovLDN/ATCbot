import { useEffect, useMemo, useState } from "react";
import { Command, defaultFilter } from "cmdk";
import * as RadixDialog from "@radix-ui/react-dialog";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { User as UserIcon, Search, CornerDownLeft } from "lucide-react";
import { buildCommands, contextBoost, type Command as Cmd } from "@/lib/commands";
import { endpoints } from "@/lib/api";
import { auth } from "@/lib/auth";
import { toast } from "@/store/toast";

/**
 * Командная палитра ⌘K.
 *
 * Собрана по ux-patterns §1. Что именно оттуда взято:
 *
 *  §1.1 Superhuman — пять команд в первом экране, у каждой значок; нечёткий
 *       поиск с прощением опечаток; контекст поднимает команду, а не удаляет
 *       остальные.
 *  §1.2 GitHub — префиксы-фильтры в одном поле: «>» действия, «@» человек.
 *       Вторая комбинация ⌘⌥K на случай, когда ⌘K перехватывает поле ввода.
 *  §1.4 NN/g — шорткат нарисован справа от пункта; это и есть обучение.
 *  §1.5 WAI-ARIA APG — внутри палитры ходят стрелками, не табом. За это, за
 *       ловушку фокуса, возврат фокуса и aria-модальность отвечает cmdk
 *       поверх Radix Dialog; свою реализацию тех же вещей мы бы отлаживали
 *       дольше, чем стоит вся палитра.
 *
 * Библиотека — cmdk 1.1.1 (42 млн загрузок в неделю, доступность заявлена и
 * протестирована автором с VoiceOver). Её граница производительности —
 * 2–3 тысячи пунктов; у нас их около тридцати плюс не больше десятка
 * найденных пользователей, так что запас двухсоткратный.
 *
 * Компонент грузится лениво (см. Layout.tsx): в первый экран приложения он не
 * входит, а Radix Dialog внутри cmdk весит заметно.
 */

const SEARCH_DEBOUNCE_MS = 250;

type Mode = "all" | "actions" | "users";

function parse(input: string): { mode: Mode; term: string } {
  if (input.startsWith(">")) return { mode: "actions", term: input.slice(1).trimStart() };
  if (input.startsWith("@")) return { mode: "users", term: input.slice(1).trimStart() };
  return { mode: "all", term: input };
}

/** Клавиша, нарисованная как клавиша. */
function Kbd({ keys }: { keys: string[] }) {
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1" aria-hidden>
      {keys.map((k, i) => (
        <kbd
          key={`${k}-${i}`}
          className="rounded-sm border border-border bg-bg-subtle px-1.5 py-0.5 font-mono text-2xs font-medium text-fg-muted"
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

export default function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [input, setInput] = useState("");
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const qc = useQueryClient();

  const { mode, term } = parse(input);

  // Поле очищается на каждое открытие: палитра, которая помнит прошлый запрос,
  // на второй раз показывает не то, что ждут.
  useEffect(() => {
    if (open) setInput("");
  }, [open]);

  const commands = useMemo(
    () =>
      buildCommands({
        navigate,
        refetchAll: () => qc.invalidateQueries(),
        logout: async () => {
          try {
            await endpoints.authLogout();
          } catch {
            //
          }
          auth.clear();
          window.location.assign("/dashboard/");
        },
        notify: (t) => toast.info(t),
      }),
    // pathname в зависимостях нарочно: часть команд (тема, шорткаты) читает
    // текущее состояние при сборке, и список надо пересобирать на каждое
    // открытие. Открытие всегда сопровождается сменой input.
    [navigate, qc, open],
  );

  const byId = useMemo(() => new Map(commands.map((c) => [c.id, c])), [commands]);

  const visible = useMemo(() => {
    if (mode === "users") return [];
    return mode === "actions" ? commands.filter((c) => c.kind === "action") : commands;
  }, [commands, mode]);

  // Первый экран — ровно пять команд, отсортированных по контексту. Superhuman
  // настаивает именно на пяти: длинный список при пустом запросе никто не
  // читает, а пять успевают заметить вместе со значками.
  const firstScreen = useMemo(
    () =>
      [...visible]
        .sort((a, b) => contextBoost(b, pathname) - contextBoost(a, pathname))
        .slice(0, 5),
    [visible, pathname],
  );

  // ── Поиск человека по «@» ────────────────────────────────────────────
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(term), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [term]);

  const users = useQuery({
    queryKey: ["palette", "userSearch", debounced],
    queryFn: () => endpoints.userSearch(debounced),
    enabled: open && mode === "users" && debounced.trim().length > 0,
    staleTime: 30_000,
  });

  /**
   * Итоговый скор пункта = нечёткое совпадение × контекст.
   *
   * Считаем максимум по названию и синонимам, а не отдаём cmdk строку value:
   * value у нас — латинский идентификатор, по нему русский запрос не найдётся.
   */
  const filter = useMemo(
    () => (value: string, search: string) => {
      const cmd: Cmd | undefined = byId.get(value);
      if (!cmd) return 0;
      const query = parse(search).term;
      if (!query) return contextBoost(cmd, pathname);
      const haystack = [cmd.title, ...(cmd.keywords ?? [])];
      const score = Math.max(...haystack.map((h) => defaultFilter(h, query, [])));
      return score * contextBoost(cmd, pathname);
    },
    [byId, pathname],
  );

  const run = (cmd: Cmd) => {
    onOpenChange(false);
    cmd.run();
  };

  const showFirstScreen = mode !== "users" && term.trim() === "";
  const list = showFirstScreen ? firstScreen : visible;

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Команды"
      shouldFilter={mode !== "users" && !showFirstScreen}
      filter={filter}
      loop
      overlayClassName="fixed inset-0 z-50 bg-bg-overlay/50"
      contentClassName="fixed left-1/2 top-[12vh] z-50 w-[min(38rem,calc(100vw-1.5rem))] -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-bg-card shadow-lg"
    >
      {/* Radix требует заголовок у модального окна, иначе окно безымянное для
          скринридера. Визуально он не нужен — поле ввода говорит само за себя. */}
      <RadixDialog.Title className="sr-only">Команды и поиск</RadixDialog.Title>

      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
        <Command.Input
          value={input}
          onValueChange={setInput}
          autoFocus
          placeholder="Команда, раздел или @человек"
          className="h-12 w-full bg-transparent text-base text-fg outline-none placeholder:text-fg-subtle"
        />
        {mode !== "all" && (
          <span className="shrink-0 rounded-sm bg-accent-3 px-1.5 py-0.5 text-2xs font-medium text-accent-text">
            {mode === "actions" ? "действия" : "человек"}
          </span>
        )}
      </div>

      <Command.List className="max-h-[min(24rem,60vh)] overflow-y-auto overscroll-contain p-1.5">
        {mode === "users" ? (
          <UserResults
            term={debounced}
            loading={users.isLoading}
            error={users.isError}
            matches={users.data?.matches ?? []}
            onPick={(tg) => {
              onOpenChange(false);
              navigate(`/users?tg=${tg}`);
            }}
          />
        ) : (
          <>
            {/* Только когда фильтрация включена: на первом экране счётчик
                совпадений всегда нулевой, и без этой проверки «ничего не
                нашлось» висело бы поверх пяти показанных команд. */}
            {!showFirstScreen && (
              <Command.Empty className="px-3 py-6 text-center text-base text-fg-muted">
                Ничего не нашлось. Попробуйте «@» и имя, чтобы искать человека.
              </Command.Empty>
            )}
            <Command.Group
              heading={
                <span className="px-2 text-2xs font-medium uppercase tracking-[0.14em] text-fg-subtle">
                  {showFirstScreen ? "Сейчас уместно" : mode === "actions" ? "Действия" : "Все команды"}
                </span>
              }
            >
              {list.map((cmd) => (
                <Command.Item
                  key={cmd.id}
                  value={cmd.id}
                  onSelect={() => run(cmd)}
                  className="flex min-h-tap-touch cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-base text-fg data-[selected=true]:bg-bg-subtle"
                >
                  <cmd.icon className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
                  <span className="truncate">{cmd.title}</span>
                  {cmd.hint && (
                    <span className="truncate text-xs text-fg-subtle">{cmd.hint}</span>
                  )}
                  {cmd.shortcut && <Kbd keys={cmd.shortcut} />}
                </Command.Item>
              ))}
            </Command.Group>
          </>
        )}
      </Command.List>

      {/* Подсказка по префиксам живёт постоянно: это единственное место, где о
          них вообще можно узнать. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border bg-bg-subtle px-3 py-2 text-2xs text-fg-subtle">
        <span className="flex items-center gap-1">
          <kbd className="rounded-sm border border-border bg-bg-card px-1 py-0.5 font-mono">&gt;</kbd>
          действия
        </span>
        <span className="flex items-center gap-1">
          <kbd className="rounded-sm border border-border bg-bg-card px-1 py-0.5 font-mono">@</kbd>
          человек
        </span>
        <span className="flex items-center gap-1">
          <kbd className="rounded-sm border border-border bg-bg-card px-1 py-0.5 font-mono">↑↓</kbd>
          выбор
        </span>
        <span className="flex items-center gap-1">
          <CornerDownLeft className="h-3 w-3" aria-hidden />
          открыть
        </span>
        <span className="ml-auto hidden sm:inline">⌘K или ⌘⌥K — вызвать откуда угодно</span>
      </div>
    </Command.Dialog>
  );
}

/** Результаты «@»: сервер уже отфильтровал, cmdk фильтровать не должен. */
function UserResults({
  term,
  loading,
  error,
  matches,
  onPick,
}: {
  term: string;
  loading: boolean;
  error: boolean;
  matches: Array<{ telegram_id: number; username: string | null; has_active_sub: boolean }>;
  onPick: (tg: number) => void;
}) {
  if (!term.trim()) {
    return (
      <div className="px-3 py-6 text-center text-base text-fg-muted">
        Введите имя, @username или telegram_id.
      </div>
    );
  }
  if (loading) {
    return <div className="px-3 py-6 text-center text-base text-fg-muted">Ищу…</div>;
  }
  // Ошибку показываем ошибкой, а не пустотой: пустой список здесь означал бы
  // «такого человека нет», и это была бы неправда.
  if (error) {
    return (
      <div className="px-3 py-6 text-center text-base text-danger">
        Поиск не ответил. Проверьте связь и повторите.
      </div>
    );
  }
  if (matches.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-base text-fg-muted">
        Никого не нашлось по «{term}».
      </div>
    );
  }
  return (
    <Command.Group>
      {matches.map((u) => (
        <Command.Item
          key={u.telegram_id}
          value={`user-${u.telegram_id}`}
          onSelect={() => onPick(u.telegram_id)}
          className="flex min-h-tap-touch cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-base text-fg data-[selected=true]:bg-bg-subtle"
        >
          <UserIcon className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
          <span className="truncate">{u.username ? `@${u.username}` : "без username"}</span>
          <span className="truncate font-mono text-xs text-fg-subtle">{u.telegram_id}</span>
          <span
            className={
              u.has_active_sub
                ? "ml-auto shrink-0 text-xs text-success"
                : "ml-auto shrink-0 text-xs text-fg-subtle"
            }
          >
            {u.has_active_sub ? "подписка активна" : "без подписки"}
          </span>
        </Command.Item>
      ))}
    </Command.Group>
  );
}
