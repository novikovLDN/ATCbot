import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, RefreshCcw, UserCircle } from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

type Row = {
  id: number;
  telegram_id: number;
  program: string;
  username: string | null;
  first_name: string | null;
  applied_at: string;
  source_broadcast_id: number | null;
};

const PROGRAM = "vpn_innovator";
const PROGRAM_LABEL = "VPN-Инноватор";
const PAGE_SIZE = 50;

export function BetaApplications() {
  const [page, setPage] = useState(0);

  const summary = useQuery({
    queryKey: ["beta-apps", "summary", PROGRAM],
    queryFn: () => endpoints.betaAppsSummary(PROGRAM),
    refetchInterval: 30_000,
  });

  const list = useQuery({
    queryKey: ["beta-apps", "list", PROGRAM, page],
    queryFn: () => endpoints.betaAppsList(page, PAGE_SIZE, PROGRAM),
    refetchInterval: 30_000,
  });

  const rows = (list.data ?? []) as unknown as Row[];
  const total = summary.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Бета-тестирование
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          {PROGRAM_LABEL}
        </h1>
        <p className="mt-2 max-w-xl text-sm text-fg-muted">
          Заявки от пользователей, нажавших кнопку «🧪 Оставить заявку» в
          рассылке. Один пользователь = одна заявка (UNIQUE по telegram_id).
          Повторный клик показывает toast «Заявка уже принята» и удаляет
          сообщение рассылки.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-2xl border border-border bg-bg-card p-4">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-fg-subtle">
            <FlaskConical className="h-3 w-3" />
            Всего заявок
          </div>
          <div className="mt-2 text-3xl font-semibold text-fg">
            {summary.isLoading ? "…" : fmtNum(total)}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-medium text-fg">Список заявок</div>
          <button
            type="button"
            onClick={() => {
              summary.refetch();
              list.refetch();
            }}
            className="pill-tab text-xs"
            title="Обновить"
          >
            <RefreshCcw className="mr-1 h-3 w-3" />
            Обновить
          </button>
        </div>

        {list.isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={FlaskConical}
            title="Заявок пока нет"
            description="Отправьте рассылку с кнопкой «🧪 Оставить заявку (VPN-Инноватор, бета-тест)» — они появятся здесь."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-bg-elevated/40 text-xs uppercase tracking-wider text-fg-subtle">
                <tr>
                  <th className="px-4 py-3 text-left">Юзер</th>
                  <th className="px-4 py-3 text-left">Username</th>
                  <th className="px-4 py-3 text-left">Telegram ID</th>
                  <th className="px-4 py-3 text-left">Подал заявку</th>
                  <th className="px-4 py-3 text-left">Из рассылки</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {rows.map((r) => (
                  <tr key={r.id} className="hover:bg-bg-elevated/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <UserCircle className="h-4 w-4 text-fg-muted" />
                        <span className="text-fg">
                          {r.first_name || "—"}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-fg-muted">
                      {r.username ? (
                        <a
                          href={`https://t.me/${r.username}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline"
                        >
                          @{r.username}
                        </a>
                      ) : (
                        <span className="text-fg-subtle">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-fg-muted">
                      {r.telegram_id}
                    </td>
                    <td className="px-4 py-3 text-fg-muted">
                      {fmtDate(r.applied_at)}
                    </td>
                    <td className="px-4 py-3 text-fg-muted">
                      {r.source_broadcast_id ? (
                        <span className="font-mono text-xs">
                          #{r.source_broadcast_id}
                        </span>
                      ) : (
                        <span className="text-fg-subtle">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {rows.length > 0 && (
          <div className="flex items-center justify-between border-t border-border px-4 py-3 text-xs text-fg-muted">
            <div>
              Страница {page + 1} · показано {rows.length} из {fmtNum(total)}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="pill-tab disabled:opacity-40"
              >
                ← Назад
              </button>
              <button
                type="button"
                onClick={() => setPage(page + 1)}
                disabled={rows.length < PAGE_SIZE}
                className="pill-tab disabled:opacity-40"
              >
                Вперёд →
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
