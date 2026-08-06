import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Power,
  RefreshCcw,
  Save,
  PlayCircle,
  Fingerprint,
  Plus,
  Trash2,
} from "lucide-react";
import {
  isPasskeySupported,
  passkeyList,
  passkeyDelete,
  registerPasskey,
  type PasskeyRow,
} from "@/lib/passkey";
import { ApiError, endpoints } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  EmptyAllClear,
  EmptyFailure,
  EmptyNotConfigured,
  Input,
  LoadingGate,
  SkeletonCard,
  SkeletonTable,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
} from "@/components/ui";
import { ReconciliationSection } from "@/components/ReconciliationSection";

/**
 * «Сервис» — уровень 3 навигации: сюда заходят редко и по конкретному поводу
 * (research §9.2). Здесь живёт здоровье интеграции с Remnawave, очереди,
 * которые могли застрять, и сверка подписок.
 *
 * ССЫЛКА СО СВОДКИ. Плитка «Расхождений с панелью» в зоне B и строки блока
 * «Требует внимания» ведут сюда с параметрами:
 *
 *     /service?focus=reconciliation              — просто открыть блок сверки
 *     /service?focus=reconciliation&tg=100500    — открыть и раскрыть карточку
 *                                                  конкретного пользователя
 *
 * Оба разбираются ниже. Параметр `tg` раньше молча игнорировался: человек
 * приходил из строки про конкретную подписку и должен был снова искать её
 * глазами в списке кандидатов — ровно та потеря контекста, ради устранения
 * которой строки делали кликабельными.
 *
 * ПРО ПРОКРУТКУ. Одного вызова scrollIntoView на монтировании мало: выше
 * блока сверки стоят четыре секции с собственными запросами, и когда их
 * данные приезжают, содержимое разъезжается вниз уже после того, как плавная
 * прокрутка закончилась. Поэтому прокрутка повторяется, пока высота страницы
 * меняется, и глохнет сама, как только та устаканилась (или через 3 секунды —
 * чтобы не бороться с человеком, если он начал листать сам).
 */
export function Service() {
  const [params] = useSearchParams();
  const focus = params.get("focus");
  const focusTelegramId = numOrNull(params.get("tg"));

  // Прокрутку к блоку включаем ТОЛЬКО когда конкретный пользователь не задан.
  // Если в адресе есть tg=, до глаз доводит сама карточка кандидата — она
  // знает своё место точнее, чем блок целиком. Оставить оба — значит стравить
  // их между собой: карточка встаёт по центру, блочная прокрутка тут же
  // утаскивает страницу к заголовку блока, и так по кругу три секунды.
  useFocusScroll(
    focus === "reconciliation" && focusTelegramId === null ? "reconciliation" : null,
  );

  return (
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Операции
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Сервис
        </h1>
        <p className="mt-1 max-w-xl text-base text-fg-muted">
          Здоровье интеграции с Remnawave, очереди, которые могли застрять, и
          сверка выданных сроков с оплаченными.
        </p>
      </header>

      <IncidentSection />
      <PendingActivationsSection />
      <PendingPaymentsSection />

      {/* Сверка Remnawave ↔ БД. Переехала со сводки: открывают редко и по
          подозрению, а места на главной занимала постоянно. */}
      <div id="reconciliation" className="scroll-mt-20">
        <ReconciliationSection focusTelegramId={focusTelegramId} />
      </div>

      <PasskeysSection />
    </div>
  );
}

/**
 * Прокрутка к якорю из ?focus=. Держится за элемент, пока страница не
 * перестанет расти под ним.
 *
 * ЧТО СЛОМАЕТСЯ ПРИ НЕВЕРНОЙ ПРАВКЕ: если убрать повтор и оставить один
 * scrollIntoView, ссылка со сводки будет промахиваться мимо блока на медленной
 * сети — визуально это выглядит как «ссылка не работает».
 */
function useFocusScroll(anchorId: string | null) {
  useEffect(() => {
    if (!anchorId) return;
    let stop = false;
    let lastTop: number | null = null;
    const started = Date.now();

    const tick = () => {
      if (stop) return;
      const el = document.getElementById(anchorId);
      if (el) {
        const top = Math.round(el.getBoundingClientRect().top);
        // lastTop === null — самый первый заход, скроллим безусловно. Иначе
        // блок, случайно оказавшийся у верхней кромки, не прокрутился бы
        // вовсе: разница с начальным значением не превысила бы порог.
        // Дальше скроллим, только пока позиция ещё едет.
        if (lastTop === null || Math.abs(top - lastTop) > 4) {
          el.scrollIntoView({ block: "start", behavior: "smooth" });
          lastTop = top;
        }
      }
      if (Date.now() - started < 3000) window.setTimeout(tick, 250);
    };
    tick();

    return () => {
      stop = true;
    };
  }, [anchorId]);
}

function numOrNull(v: string | null): number | null {
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Режим инцидента: баннер всем пользователям бота. */
function IncidentSection() {
  const qc = useQueryClient();
  const incident = useQuery({
    queryKey: ["incident"],
    queryFn: endpoints.incidentGet,
    refetchInterval: 60_000,
  });

  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (incident.data && !dirty) {
      setText(incident.data.incident_text ?? "");
    }
  }, [incident.data, dirty]);

  const save = useMutation({
    mutationFn: (body: { is_active: boolean; incident_text?: string | null }) =>
      endpoints.incidentSet(body),
    onSuccess: (data) => {
      toast.success(
        data.is_active ? "Инцидент-режим включён" : "Инцидент-режим выключен",
      );
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["incident"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сохранить"),
  });

  const isActive = incident.data?.is_active ?? false;

  return (
    <Card className={isActive ? "border-warning" : undefined}>
      <CardHeader
        title="Режим инцидента"
        subtitle="баннер появится у каждого пользователя на главном экране бота"
        actions={
          // Пока состояние не приехало, переключать нечего: иначе можно
          // случайно «включить» уже включённый режим и погасить баннер.
          incident.isError ? null : (
            <>
              {isActive && <StatusBadge kind="risk">Баннер показывается</StatusBadge>}
              <Button
                variant={isActive ? "danger" : "primary"}
                onClick={() =>
                  save.mutate({ is_active: !isActive, incident_text: text || null })
                }
                loading={save.isPending}
                disabled={incident.isLoading}
                icon={<Power className="h-3.5 w-3.5" />}
              >
                {isActive ? "Выключить" : "Включить"}
              </Button>
            </>
          )
        }
      />
      <CardBody className="space-y-3">
        {incident.isError ? (
          <EmptyFailure
            what="состояние режима инцидента"
            reason={
              (incident.error as ApiError)?.detail ??
              "Не знаем, показывается ли сейчас баннер. Переключатель спрятан намеренно: включать вслепую нельзя."
            }
            onRetry={() => incident.refetch()}
          />
        ) : (
          <>
            <p className="text-base text-fg-muted">
              Текст для предупреждений о технических работах и перебоях с
              оплатой. Разметка — HTML, как в Telegram.
            </p>
            <label className="block">
              <div className="mb-1.5 text-xs font-medium text-fg-subtle">
                Текст баннера
              </div>
              <textarea
                className="input min-h-[120px] resize-y leading-relaxed"
                value={text}
                maxLength={2000}
                onChange={(e) => {
                  setText(e.target.value);
                  setDirty(true);
                }}
                placeholder="Например: Сейчас наблюдаются перебои с оплатой через СБП. Используйте карту."
              />
            </label>

            {dirty && (
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => {
                    setText(incident.data?.incident_text ?? "");
                    setDirty(false);
                  }}
                  disabled={save.isPending}
                >
                  Вернуть как было
                </Button>
                <Button
                  variant="primary"
                  onClick={() =>
                    save.mutate({ is_active: isActive, incident_text: text })
                  }
                  loading={save.isPending}
                  icon={<Save className="h-3.5 w-3.5" />}
                >
                  Сохранить текст
                </Button>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

/** Очередь провизии VPN: оплачено, но ключ ещё не выдан. */
function PendingActivationsSection() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["activations", "pending"],
    queryFn: () => endpoints.activationsPending(200),
    refetchInterval: 15_000,
  });

  const retry = useMutation({
    mutationFn: (subscriptionId: number) =>
      endpoints.activationRetry(subscriptionId),
    onSuccess: (data) => {
      if (data.ok) {
        toast.success(`Подписка #${data.subscription_id} активирована`);
      } else {
        toast.error(
          data.error_message ??
            `Повтор для #${data.subscription_id} не удался — заявка осталась в очереди`,
        );
      }
      qc.invalidateQueries({ queryKey: ["activations"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось запустить повтор"),
  });

  const total = list.data?.total ?? 0;
  const rows = list.data?.rows ?? [];

  return (
    <Card>
      <CardHeader
        title="Очередь выдачи VPN"
        subtitle="оплачено, подписка создана, но ключ ещё не выдан — VPN-API не ответил в момент вебхука"
        actions={
          <>
            {total > 0 && <StatusBadge kind="pending">{fmtNum(total)} в очереди</StatusBadge>}
            <Button
              onClick={() => list.refetch()}
              loading={list.isFetching}
              icon={<RefreshCcw className="h-3.5 w-3.5" />}
            >
              Обновить
            </Button>
          </>
        }
      />
      <CardBody className="space-y-3">
        {/* Отказ запроса ОБЯЗАН стоять перед пустым состоянием. Иначе
            недоступный бэкенд выглядел бы как «очередь пуста, всё хорошо» —
            то есть ровно наоборот. */}
        {list.isError ? (
          <EmptyFailure
            what="очередь выдачи VPN"
            reason={
              (list.error as ApiError)?.detail ??
              "Запрос не вернулся. Пустая очередь и недоступный сервер выглядят одинаково, поэтому здесь ошибка, а не «всё хорошо»."
            }
            onRetry={() => list.refetch()}
          />
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={<SkeletonTable rows={3} cols={6} />}
            message="Читаю очередь выдачи"
          >
            {rows.length === 0 ? (
              <EmptyAllClear
                title="Очередь пуста"
                description="У всех оплаченных подписок есть VPN-ключи. Это норма — фоновый воркер повторяет выдачу раз в 5 минут."
              />
            ) : (
              <>
                <p className="text-base text-fg-muted">
                  Фоновый воркер повторяет выдачу раз в 5 минут, максимум 5
                  попыток. Кнопка «Повторить» запускает попытку сейчас.
                </p>
                <TableScroll>
                  <Table density="compact">
                    <THead>
                      <tr>
                        <TH>Подписка</TH>
                        <TH>Пользователь</TH>
                        <TH>Тариф</TH>
                        <TH>Попыток</TH>
                        <TH>Последняя ошибка</TH>
                        <TH>С какого времени</TH>
                        <TH />
                      </tr>
                    </THead>
                    <TBody>
                      {rows.map((r) => {
                        const id = Number(r.id ?? 0);
                        const attempts = asNum(r.activation_attempts) ?? 0;
                        const err = String(r.last_activation_error ?? "—");
                        const since =
                          typeof r.activated_at === "string"
                            ? fmtDate(r.activated_at)
                            : "—";
                        return (
                          <TR key={id || Math.random()}>
                            <TD mono>{id}</TD>
                            <TD>tg:{String(r.telegram_id ?? "—")}</TD>
                            <TD>{String(r.subscription_type ?? "—")}</TD>
                            <TD>
                              {/* Число попыток подписано словом, а не только
                                  цветом бейджа (research §4.11). */}
                              <StatusBadge
                                kind={
                                  attempts >= 5
                                    ? "failure"
                                    : attempts >= 3
                                      ? "risk"
                                      : "neutral"
                                }
                              >
                                {attempts} из 5
                              </StatusBadge>
                            </TD>
                            <TD className="max-w-[280px] truncate text-xs text-fg-muted">
                              {err}
                            </TD>
                            <TD className="text-xs text-fg-muted">{since}</TD>
                            <TD numeric>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => retry.mutate(id)}
                                loading={retry.isPending && retry.variables === id}
                                disabled={retry.isPending}
                                icon={<PlayCircle className="h-3.5 w-3.5" />}
                              >
                                Повторить
                              </Button>
                            </TD>
                          </TR>
                        );
                      })}
                    </TBody>
                  </Table>
                </TableScroll>
              </>
            )}
          </LoadingGate>
        )}
      </CardBody>
    </Card>
  );
}

/** Платежи, застрявшие в статусе «в обработке». */
function PendingPaymentsSection() {
  const list = useQuery({
    queryKey: ["payments", "pending"],
    queryFn: endpoints.paymentsPending,
    refetchInterval: 15_000,
  });

  const count = list.data?.length ?? 0;

  return (
    <Card>
      <CardHeader
        title="Висящие платежи"
        subtitle="статус «в обработке» дольше обычного · обновляется само раз в 15 секунд"
        actions={
          <>
            {count > 0 && <StatusBadge kind="pending">{fmtNum(count)}</StatusBadge>}
            <Button
              onClick={() => list.refetch()}
              loading={list.isFetching}
              icon={<RefreshCcw className="h-3.5 w-3.5" />}
            >
              Обновить
            </Button>
          </>
        }
      />
      <CardBody className="space-y-3">
        {list.isError ? (
          <EmptyFailure
            what="висящие платежи"
            reason={
              (list.error as ApiError)?.detail ??
              "Запрос не вернулся. «Висящих платежей нет» здесь было бы враньём — мы просто не знаем."
            }
            onRetry={() => list.refetch()}
          />
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={<SkeletonTable rows={3} cols={6} />}
            message="Ищу зависшие платежи"
          >
            {count === 0 ? (
              <EmptyAllClear
                title="Висящих платежей нет"
                description="Все платежи дошли до конечного статуса."
              />
            ) : (
              <>
                <p className="text-base text-fg-muted">
                  Платежи виснут обычно из-за потерянных вебхуков провайдера.
                  Большинство расходится само в течение часа. Если запись висит
                  дольше суток — стоит разобрать руками.
                </p>
                <TableScroll>
                  <Table density="compact">
                    <THead>
                      <tr>
                        <TH>ID</TH>
                        <TH>Пользователь</TH>
                        <TH>Тариф</TH>
                        <TH numeric>Сумма</TH>
                        <TH>Провайдер</TH>
                        <TH>Создан</TH>
                      </tr>
                    </THead>
                    <TBody>
                      {(list.data ?? []).map((p) => (
                        <TR key={String(p.id ?? Math.random())}>
                          <TD mono>{String(p.id ?? "—")}</TD>
                          <TD>tg:{String(p.telegram_id ?? "—")}</TD>
                          <TD>{String(p.tariff ?? "—")}</TD>
                          <TD numeric>
                            {typeof p.amount === "number"
                              ? fmtRub(p.amount / 100)
                              : String(p.amount ?? "—")}
                          </TD>
                          <TD className="text-fg-muted">
                            {String(p.source ?? "—")}
                          </TD>
                          <TD className="text-fg-muted">
                            {typeof p.created_at === "string"
                              ? fmtDate(p.created_at)
                              : "—"}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableScroll>
              </>
            )}
          </LoadingGate>
        )}
      </CardBody>
    </Card>
  );
}

/** Вход по Face ID / Touch ID. */
function PasskeysSection() {
  const qc = useQueryClient();
  const supported = isPasskeySupported();

  const list = useQuery({
    queryKey: ["passkeys", "list"],
    queryFn: passkeyList,
    enabled: supported,
  });

  const [label, setLabel] = useState("");
  const [toDelete, setToDelete] = useState<PasskeyRow | null>(null);

  const add = useMutation({
    mutationFn: () => registerPasskey(label.trim() || undefined),
    onSuccess: () => {
      toast.success("Passkey добавлен");
      setLabel("");
      qc.invalidateQueries({ queryKey: ["passkeys"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { detail?: string })?.detail;
      // «cancelled» — человек сам закрыл системное окно. Это не ошибка.
      if (detail !== "cancelled") {
        toast.error(detail ?? "Не удалось добавить");
      }
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => passkeyDelete(id),
    onSuccess: () => {
      toast.success("Ключ удалён");
      setToDelete(null);
      qc.invalidateQueries({ queryKey: ["passkeys"] });
    },
    onError: () => toast.error("Не удалось удалить"),
  });

  if (!supported) {
    return (
      <Card>
        <CardHeader title="Вход по Face ID / Touch ID" />
        <CardBody>
          <EmptyNotConfigured
            title="Браузер не поддерживает passkey"
            description="Откройте дашборд в Safari (iOS или macOS) либо в Chrome — там доступны Face ID, Touch ID и системные ключи."
            icon={Fingerprint}
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Вход по Face ID / Touch ID"
        subtitle="passkey привязывается к устройству или к связке ключей iCloud"
      />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-1 items-end gap-2 md:grid-cols-[1fr_auto]">
          <Input
            label="Метка нового ключа"
            hint="Чтобы отличать устройства в списке ниже"
            maxLength={64}
            placeholder="iPhone 15, MacBook Air"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <Button
            variant="primary"
            onClick={() => add.mutate()}
            loading={add.isPending}
            icon={<Plus className="h-3.5 w-3.5" />}
          >
            Добавить passkey
          </Button>
        </div>

        {list.isError ? (
          <EmptyFailure
            what="список ключей"
            reason={
              (list.error as ApiError)?.detail ??
              "Запрос не вернулся. Раньше на этом месте писали «ещё ни одного ключа» — и это было неправдой."
            }
            onRetry={() => list.refetch()}
          />
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={<SkeletonCard lines={2} />}
            message="Читаю список ключей"
          >
            {!list.data || list.data.length === 0 ? (
              <EmptyNotConfigured
                title="Ключей пока нет"
                description="Добавьте passkey — и в следующий раз вход займёт одно касание, без пароля."
                icon={Fingerprint}
              />
            ) : (
              <ul className="divide-y divide-border-subtle rounded-lg border border-border">
                {list.data.map((p: PasskeyRow) => (
                  <li
                    key={p.id}
                    className="flex items-center justify-between gap-2 px-3 py-3 text-base"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-fg">
                        {p.label || "Passkey"}
                      </div>
                      <div className="mt-0.5 text-xs text-fg-muted">
                        {p.transports && p.transports.length > 0
                          ? `${p.transports.join(" · ")} · `
                          : ""}
                        добавлен{" "}
                        {p.created_at
                          ? new Date(p.created_at).toLocaleDateString("ru-RU")
                          : "—"}
                        {p.last_used_at
                          ? ` · последний вход ${new Date(p.last_used_at).toLocaleDateString("ru-RU")}`
                          : " · ещё не использовался"}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setToDelete(p)}
                      icon={<Trash2 className="h-3.5 w-3.5" />}
                    >
                      Удалить
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </LoadingGate>
        )}
      </CardBody>

      {/* Удаление ключа необратимо и может отрезать вход с устройства, к
          которому нет доступа прямо сейчас, — поэтому диалог, а не окно
          отмены (research §6.6). */}
      <ConfirmDialog
        open={toDelete !== null}
        onCancel={() => setToDelete(null)}
        onConfirm={() => toDelete && del.mutate(toDelete.id)}
        title={`Удалить ключ «${toDelete?.label || "Passkey"}»?`}
        body="Войти этим устройством без пароля больше не получится. Ключ придётся заводить заново с самого устройства."
        confirmLabel="Удалить ключ"
        cancelLabel="Оставить"
        destructive
      />
    </Card>
  );
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
