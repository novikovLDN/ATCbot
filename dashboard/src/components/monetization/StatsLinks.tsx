import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { ApiError, endpoints, type StatsLinkRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  EmptyFailure,
  EmptyFirstRun,
  Input,
  LoadingGate,
  SkeletonCard,
  StatusBadge,
} from "@/components/ui";
import { CopyField } from "./CopyField";
import { linkState } from "./labels";

/**
 * Статистические ссылки: клик → регистрация → триал → покупка.
 *
 * ЛИМИТ АКТИВНЫХ ВИДЕН ДО НАЖАТИЯ. Сервер разрешает не больше десяти
 * активных ссылок и отвечает 409 на одиннадцатую. Раньше об этом узнавали
 * из красного тоста после того, как форма уже заполнена; теперь счётчик
 * «7 из 10» стоит рядом с кнопкой, а на исходе лимита кнопка выключается
 * и объясняет, что делать.
 *
 * УДАЛЕНИЕ ТРЕБУЕТ НАБРАТЬ ИМЯ ССЫЛКИ. Вместе со ссылкой уходит вся её
 * статистика — это необратимо и восстановлению не подлежит. Приём GitHub
 * Danger Zone: набирается не слово «удалить», а точное имя объекта, иначе
 * можно снести не ту ссылку (ux-patterns §2.3).
 */

/** Столько активных ссылок разрешает сервер (MAX_ACTIVE_STATS_LINKS). */
const MAX_ACTIVE = 10;

export function StatsLinks() {
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const list = useQuery({
    queryKey: ["links", "stats"],
    queryFn: endpoints.statsLinksList,
    refetchInterval: 60_000,
  });

  const rows = list.data ?? [];
  const activeCount = rows.filter((r) => r.is_active).length;
  const limitReached = activeCount >= MAX_ACTIVE;

  const create = useMutation({
    mutationFn: () => endpoints.statsLinkCreate({ name: name.trim() }),
    onSuccess: () => {
      toast.success("Ссылка создана — скопируйте её из списка");
      setName("");
      qc.invalidateQueries({ queryKey: ["links", "stats"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось создать ссылку"),
  });

  const nameValid = name.trim().length > 0 && name.trim().length <= 80;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Новая ссылка со статистикой"
          subtitle={`активных ${activeCount} из ${MAX_ACTIVE} · больше сервер не разрешит`}
        />
        <CardBody>
          <form
            className="flex flex-col gap-2 md:flex-row md:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (nameValid && !limitReached) create.mutate();
            }}
          >
            <div className="flex-1">
              <Input
                label="Название"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={80}
                placeholder="Пост в инстаграме, декабрь"
                hint="Видно только вам — по нему потом ищут строку в списке"
                disabled={limitReached}
              />
            </div>
            <Button
              type="submit"
              variant="primary"
              icon={<Plus className="h-3.5 w-3.5" aria-hidden />}
              disabled={!nameValid || limitReached}
              loading={create.isPending}
            >
              Создать
            </Button>
          </form>
          {limitReached && (
            <p className="mt-2 text-base text-fg-muted">
              Лимит активных ссылок выбран. Отключите ненужную в списке ниже —
              её статистика при этом сохранится, — и место освободится.
            </p>
          )}
        </CardBody>
      </Card>

      {list.isError ? (
        <EmptyFailure
          what="список ссылок со статистикой"
          reason="Список не пришёл. Пустой экран здесь читался бы как «ссылок нет» — это не так, это отказ запроса."
          onRetry={() => list.refetch()}
        />
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={
            <div className="space-y-3">
              <SkeletonCard lines={4} />
              <SkeletonCard lines={4} />
            </div>
          }
          message="Считаю переходы по ссылкам"
        >
          {/* `list.data &&` обязателен: первую секунду рисуются дети, а не
              скелетон, и без проверки мелькало бы «ссылок ещё нет». */}
          {list.data && rows.length === 0 ? (
            <EmptyFirstRun
              title="Ссылок со статистикой ещё нет"
              description="Такая ссылка считает переходы и доводит их до покупки: сколько человек кликнуло, сколько зарегистрировалось, сколько взяло триал и сколько заплатило."
            />
          ) : (
            <div className="space-y-3">
              {rows.map((row) => (
                <StatsLinkCard key={row.id} row={row} />
              ))}
            </div>
          )}
        </LoadingGate>
      )}
    </div>
  );
}

function StatsLinkCard({ row }: { row: StatsLinkRow }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const state = linkState(row);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["links", "stats"] });

  const toggle = useMutation({
    mutationFn: () =>
      row.is_active
        ? endpoints.statsLinkDeactivate(row.id)
        : endpoints.statsLinkReactivate(row.id),
    onSuccess: () => {
      toast.success(
        row.is_active
          ? `«${row.name ?? row.slug}» отключена — переходы больше не засчитываются`
          : `«${row.name ?? row.slug}» снова считает переходы`,
      );
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось переключить ссылку"),
  });

  const del = useMutation({
    mutationFn: () => endpoints.statsLinkDelete(row.id),
    onSuccess: () => {
      toast.success(`«${row.name ?? row.slug}» удалена вместе со статистикой`);
      setConfirmDelete(false);
      invalidate();
    },
    onError: (e: unknown) => toast.error((e as ApiError)?.detail ?? "Не удалось удалить ссылку"),
  });

  const clicks = Number(row.total_clicks ?? 0);
  const paid = Number(row.paid_users ?? 0);
  // Конверсия «клик → покупка». Считается только когда есть от чего
  // считать: «0 % при нуле кликов» — не ноль, а «ещё не измеряли».
  const conversion = clicks > 0 ? (paid / clicks) * 100 : null;

  return (
    <Card className={cn(!row.is_active && "opacity-80")}>
      <CardHeader
        title={row.name || row.slug}
        subtitle={
          <>
            создана {fmtDate(row.created_at)}
            {!row.is_active && row.deactivated_at
              ? ` · отключена ${fmtDate(row.deactivated_at)}`
              : ""}
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
            <Button size="sm" onClick={() => toggle.mutate()} loading={toggle.isPending}>
              {row.is_active ? "Отключить" : "Включить"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}>
              Удалить
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-3">
        <CopyField value={row.t_me_url} label="ссылку" />

        {/* Воронка слева направо: она и читается как воронка. */}
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Funnel label="Переходов" value={fmtNum(row.total_clicks)} />
          <Funnel label="Человек" value={fmtNum(row.unique_visitors)} />
          <Funnel label="Новых" value={fmtNum(row.new_users)} />
          <Funnel label="Взяли триал" value={fmtNum(row.trials_activated)} />
          <Funnel label="Заплатили" value={fmtNum(row.paid_users)} />
          <Funnel label="Выручка" value={fmtRub(row.total_revenue_rubles)} />
        </dl>

        <p className="text-xs text-fg-muted">
          {conversion === null
            ? "По ссылке ещё не переходили — конверсию считать не из чего."
            : `Из переходов покупкой заканчивается ${conversion.toFixed(1)} %.`}{" "}
          Выручка — внешние поступления от людей, пришедших по этой ссылке.
        </p>
      </CardBody>

      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => del.mutate()}
        loading={del.isPending}
        destructive
        title={`Удалить ссылку «${row.name ?? row.slug}»`}
        confirmLabel="Удалить ссылку"
        cancelLabel="Не удалять"
        requireText={row.name || row.slug}
        requireHint={`Наберите название ссылки — «${row.name || row.slug}»`}
        body={
          <>
            Вместе со ссылкой исчезнет вся её статистика:{" "}
            {fmtNum(row.total_clicks)} переходов, {fmtNum(row.paid_users)} покупок,{" "}
            {fmtRub(row.total_revenue_rubles)} выручки. Восстановить эти числа
            нечем. Сама ссылка перестанет работать у всех, кому она разослана.
            <div className="mt-2">
              Если нужно просто прекратить приём переходов — отключите ссылку,
              статистика при этом останется.
            </div>
          </>
        }
      />
    </Card>
  );
}

function Funnel({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border px-2 py-1.5">
      <dt className="text-2xs text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 truncate text-base font-semibold tabular-nums text-fg">{value}</dd>
    </div>
  );
}
