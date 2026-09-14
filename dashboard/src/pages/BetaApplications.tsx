import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCcw, UserCircle } from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

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
    <div>
      <PageHeader
        title={PROGRAM_LABEL}
        sub="Заявки от пользователей, нажавших кнопку «🧪 Оставить заявку» в рассылке. Один пользователь = одна заявка (UNIQUE по telegram_id). Повторный клик показывает toast «Заявка уже принята» и удаляет сообщение рассылки."
      />

      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-4"
          variant="accent"
          label="Всего заявок"
          value={fmtNum(total)}
          sub="Бета-тестирование"
          loading={summary.isLoading}
        />

        <Surface
          className="sm:col-span-6 xl:col-span-12"
          label="Список заявок"
          aside={
            <button
              type="button"
              onClick={() => {
                summary.refetch();
                list.refetch();
              }}
              className="btn-secondary"
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              Обновить
            </button>
          }
        >
          {list.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : list.isError && !list.data ? (
            <ErrorState error={list.error} onRetry={() => list.refetch()} className="bg-tile-3" />
          ) : rows.length === 0 ? (
            <EmptyState
              title="Заявок пока нет"
              hint="Отправьте рассылку с кнопкой «🧪 Оставить заявку (VPN-Инноватор, бета-тест)» — они появятся здесь."
            />
          ) : (
            <div className="-mx-2 overflow-x-auto px-2">
              <table className="dtable min-w-[640px]">
                <thead>
                  <tr>
                    <th>Юзер</th>
                    <th>Username</th>
                    <th>Telegram ID</th>
                    <th>Подал заявку</th>
                    <th>Из рассылки</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <span className="flex items-center gap-2">
                          <UserCircle className="t-mute h-4 w-4 flex-none" aria-hidden="true" />
                          <span>{r.first_name || "—"}</span>
                        </span>
                      </td>
                      <td>
                        {r.username ? (
                          <a
                            href={`https://t.me/${r.username}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="link-accent"
                          >
                            @{r.username}
                          </a>
                        ) : (
                          <span className="t-mute">—</span>
                        )}
                      </td>
                      <td className="t-body tabular font-mono text-[12px]">{r.telegram_id}</td>
                      <td className="t-body tabular whitespace-nowrap">{fmtDate(r.applied_at)}</td>
                      <td className="t-body">
                        {r.source_broadcast_id ? (
                          <span className="tabular font-mono text-[12px]">#{r.source_broadcast_id}</span>
                        ) : (
                          <span className="t-mute">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {rows.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <p className="t-mute tabular text-[13px]">
                Страница {page + 1} · показано {rows.length} из {fmtNum(total)}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setPage(Math.max(0, page - 1))}
                  disabled={page === 0}
                  className="btn-secondary"
                >
                  ← Назад
                </button>
                <button
                  type="button"
                  onClick={() => setPage(page + 1)}
                  disabled={rows.length < PAGE_SIZE}
                  className="btn-secondary"
                >
                  Вперёд →
                </button>
              </div>
            </div>
          )}
        </Surface>
      </Bento>
    </div>
  );
}
