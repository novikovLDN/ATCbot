import { useState } from "react";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import {
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  ConfirmDialog,
  Dash,
  DensityToggle,
  EmptyAllClear,
  EmptyFailure,
  EmptyFilter,
  EmptyFirstRun,
  EmptyNoAccess,
  EmptyNotConfigured,
  Input,
  StatCard,
  LoadingGate,
  Modal,
  ProgressBar,
  SkeletonCard,
  SkeletonTable,
  SkeletonTile,
  Spinner,
  StatTile,
  StatusBadge,
  StatusDot,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
  UndoBanner,
  type Density,
} from "@/components/ui";
import { getTheme, setTheme, type Theme } from "@/lib/theme";

/**
 * Витрина примитивов. Доступна только в dev-режиме — см. App.tsx, где ветка
 * закрыта проверкой import.meta.env.DEV. В продовой сборке страницы нет.
 *
 * Зачем она: примитивы проверяются глазами разом, в обеих темах и на обеих
 * плотностях. Иначе расхождения всплывают уже на экранах, когда переделывать
 * дорого.
 */
export default function UiKit() {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [density, setDensity] = useState<Density>("comfortable");
  const [modal, setModal] = useState(false);
  const [confirmSoft, setConfirmSoft] = useState(false);
  const [confirmHard, setConfirmHard] = useState(false);
  const [undo, setUndo] = useState(false);
  const [loading, setLoading] = useState(false);

  const switchTheme = (t: Theme) => {
    setTheme(t);
    setThemeState(t);
  };

  return (
    <div className="min-h-full bg-bg p-6 text-fg">
      <div className="mx-auto flex max-w-5xl flex-col gap-8">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">Примитивы</h1>
            <p className="mt-1 text-base text-fg-muted">
              Витрина для проверки токенов и компонентов. Только dev-режим.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {(["light", "dark", "system"] as Theme[]).map((t) => (
              <Button
                key={t}
                size="sm"
                variant={theme === t ? "primary" : "secondary"}
                onClick={() => switchTheme(t)}
              >
                {t === "light" ? "Светлая" : t === "dark" ? "Тёмная" : "Системная"}
              </Button>
            ))}
          </div>
        </header>

        <Section title="Шкала цвета" note="12 ступеней: 1–2 фон, 3–5 компонент, 6–8 границы, 9–10 заливка, 11–12 текст">
          <Ramp prefix="bg-n-" label="Нейтральная" />
          <Ramp prefix="bg-accent-" label="Акцент (кобальт)" />
          {/* Классы перечислены целиком, а не собраны из переменной: Tailwind
              читает исходники как текст и склеенных имён не видит. */}
          <div className="flex flex-wrap gap-2">
            {SEMANTIC.map(([name, solidCls, textCls]) => (
              <div key={name} className="flex items-center gap-2 rounded-md border border-border px-2 py-1">
                <span className={`h-4 w-4 rounded-sm ${solidCls}`} />
                <span className={`text-xs ${textCls}`}>{name}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Типографика" note="базовый размер 14px, начертания 400/500/600">
          <div className="space-y-1">
            <div className="text-4xl font-semibold">1 284 590 ₽ — деньги сегодня</div>
            <div className="text-3xl font-semibold">32 px, число первого уровня</div>
            <div className="text-2xl font-semibold">24 px, число второго уровня</div>
            <div className="text-xl font-semibold">20 px, заголовок раздела</div>
            <div className="text-lg font-medium">16 px, заголовок карточки</div>
            <div className="text-base">14 px, основной текст интерфейса. Русский и Latin вместе.</div>
            <div className="text-sm">13 px, плотная таблица</div>
            <div className="text-xs text-fg-muted">12 px, подписи</div>
            <div className="text-2xs uppercase tracking-wide text-fg-subtle">11 PX, МИКРО-МЕТКА</div>
            <div className="pt-2 font-mono text-xs">
              tg:100500 · 0O0O · a9f3e0 · 1 490,00 ₽ — моноширинный, перечёркнутый ноль
            </div>
            <table className="mt-2 text-base">
              <tbody>
                <tr>
                  <td className="pr-4 text-right tabular-nums">1 111,00 ₽</td>
                  <td className="text-fg-muted">табличные цифры: разряды стоят</td>
                </tr>
                <tr>
                  <td className="pr-4 text-right tabular-nums">98 764,50 ₽</td>
                  <td className="text-fg-muted">друг под другом и не пляшут</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Кнопки">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />}>
              Создать промокод
            </Button>
            <Button>Обновить</Button>
            <Button variant="ghost" icon={<RefreshCw className="h-3.5 w-3.5" />}>
              Перечитать
            </Button>
            <Button variant="danger" icon={<Trash2 className="h-3.5 w-3.5" />}>
              Отозвать доступ
            </Button>
            <Button loading>Отправляю</Button>
            <Button disabled>Недоступно</Button>
            <Button size="sm">Мелкая</Button>
            <Button size="sm" shortcut="⌘K">
              С шорткатом
            </Button>
          </div>
        </Section>

        <Section title="Поля ввода">
          <div className="grid gap-3 sm:grid-cols-2">
            <Input label="Telegram ID" placeholder="100500" mono hint="Числовой ID, без @" />
            <Input label="Промокод" defaultValue="SUMMER25" mono />
            <Input label="Сумма возврата" defaultValue="1490" trailing="₽" />
            <Input
              label="Почта"
              defaultValue="не почта"
              error="Похоже на опечатку: в адресе нет @"
            />
          </div>
        </Section>

        <Section title="Состояния" note="цвет + иконка + слово, шесть смыслов">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge kind="success">Оплачено</StatusBadge>
            <StatusBadge kind="pending">В обработке</StatusBadge>
            <StatusBadge kind="risk">Просрочено</StatusBadge>
            <StatusBadge kind="failure">Возврат</StatusBadge>
            <StatusBadge kind="info">Система</StatusBadge>
            <StatusBadge kind="neutral">Архив</StatusBadge>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge kind="success" solid>
              Оплачено
            </StatusBadge>
            <StatusBadge kind="failure" solid>
              Возврат
            </StatusBadge>
            <StatusDot kind="success" label="соединение активно" />
            <StatusDot kind="risk" label="расхождение с панелью" />
          </div>
        </Section>

        <Section title="Карточки и плитки" note="ни градиента под числом, ни иконки рядом с ним — и то и другое мешает искать нужное число глазами">
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile label="Доход сегодня" value="84 320 ₽" hint="вчера в это же время 71 200 ₽" tone="money-in" />
            <StatTile label="Возвраты за 24 ч" value="−4 470 ₽" hint="3 операции" tone="money-out" />
            <StatTile label="Активные подписки" value="1 284" hint="+12 за сутки" />
          </div>
          {/* StatCard отличается от StatTile крупнее числом и наличием
              состояния загрузки — он для страниц аналитики, где числа
              приезжают отдельными запросами. */}
          <div className="grid gap-3 sm:grid-cols-3">
            <StatCard label="Доход за 30 дней" value="1 284 590 ₽" tone="success" pill="+12%" />
            <StatCard label="ARPU" hint="на всю базу" value="184 ₽" tone="accent" />
            <StatCard label="Средний чек" value="—" loading />
          </div>
          <Card>
            <CardHeader
              title="Сверка с VPN-панелью"
              subtitle="последняя проверка 4 минуты назад"
              actions={<Button size="sm">Проверить</Button>}
            />
            <CardBody className="text-base text-fg-muted">
              Расхождений не найдено. Тело карточки — обычный текст без градиента под ним.
            </CardBody>
            <CardFooter>
              <Button size="sm">Открыть журнал</Button>
            </CardFooter>
          </Card>
        </Section>

        <Section title="Таблица" note="липкая шапка, стрелки ходят по строкам, Enter открывает">
          <DensityToggle value={density} onChange={setDensity} />
          <TableScroll className="max-h-64">
            <Table density={density}>
              <THead>
                <tr>
                  <TH>Пользователь</TH>
                  <TH>Тариф</TH>
                  <TH>Статус</TH>
                  <TH numeric>Сумма</TH>
                  <TH>ID платежа</TH>
                </tr>
              </THead>
              <TBody>
                {ROWS.map((r, i) => (
                  <TR key={r.id} interactive first={i === 0} tone={r.tone} onActivate={() => setModal(true)}>
                    <TD>{r.user}</TD>
                    <TD>{r.plan ?? <Dash />}</TD>
                    <TD>
                      <StatusBadge kind={r.kind}>{r.status}</StatusBadge>
                    </TD>
                    <TD numeric>{r.sum}</TD>
                    <TD mono>{r.id}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        </Section>

        <Section title="Загрузка" note="до 1 с ничего · 1–2 с скелетон · 2–10 с крутилка · дальше процент и «Прервать»">
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => setLoading((v) => !v)}>
              {loading ? "Остановить" : "Запустить лестницу"}
            </Button>
            <Spinner label="Считаю платежи за 30 дней" />
          </div>
          <LoadingGate
            loading={loading}
            skeleton={<SkeletonTable rows={3} cols={4} />}
            message="Считаю платежи за 30 дней"
            onAbort={() => setLoading(false)}
          >
            <div className="rounded-lg border border-border p-4 text-base text-fg-muted">
              Данные на месте. Нажмите «Запустить лестницу» и следите за сменой ступеней.
            </div>
          </LoadingGate>
          <ProgressBar value={62} label="Рассылка: отправлено 774 из 1 248" onAbort={() => {}} />
          <div className="grid gap-3 sm:grid-cols-3">
            <SkeletonTile />
            <SkeletonCard />
            <SkeletonTable rows={3} cols={3} />
          </div>
        </Section>

        <Section
          title="Пустые состояния"
          note="шесть разных случаев, у каждого свой текст и своё действие · порядок проверки на экране: отказ → нет прав → не настроено → всё в порядке → фильтр → первый запуск"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <EmptyFirstRun
              title="Промокодов пока нет"
              description="Промокод даёт скидку на первый платёж и помогает считать эффект от рекламы."
              actionLabel="Создать промокод"
              onAction={() => {}}
            />
            <EmptyFilter query="ivan" onReset={() => {}} />
            <EmptyAllClear
              title="Расхождений с панелью нет"
              description="Все сроки в Remnawave укладываются в оплаченный период. Проверка прошла целиком."
              actionLabel="Проверить ещё раз"
              onAction={() => {}}
            />
            <EmptyNotConfigured
              title="Push на этом устройстве не подключён"
              description="Нажмите «Подключить» и разрешите уведомления в браузере. На iPhone сначала добавьте дашборд на экран «Домой»."
              actionLabel="Подключить"
              onAction={() => {}}
            />
            <EmptyNoAccess what="журналу действий" contact="владелец панели" />
            <EmptyFailure what="платежи за 30 дней" onRetry={() => {}} />
          </div>
        </Section>

        <Section title="Опасные действия" note="обратимое — баннер отмены; необратимое — диалог с вводом идентификатора">
          <UndoBanner
            open={undo}
            message="Доступ пользователя @ivanov отозван."
            seconds={10}
            onUndo={() => setUndo(false)}
            onDismiss={() => setUndo(false)}
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setUndo(true)}>Отозвать доступ (обратимо)</Button>
            <Button onClick={() => setConfirmSoft(true)}>Вернуть деньги</Button>
            <Button variant="danger" onClick={() => setConfirmHard(true)}>
              Удалить клиента
            </Button>
            <Button onClick={() => setModal(true)}>Открыть модалку</Button>
          </div>
        </Section>
      </div>

      <Modal
        open={modal}
        onClose={() => setModal(false)}
        title="Платёж 8f2a91"
        description="Пример модального окна: фокус заперт внутри, Esc закрывает."
        footer={
          <>
            <Button onClick={() => setModal(false)}>Закрыть</Button>
            <Button variant="primary" onClick={() => setModal(false)}>
              Открыть в платежах
            </Button>
          </>
        }
      >
        <div className="space-y-2 text-base text-fg-muted">
          <div>Пользователь: @ivanov</div>
          <div>Провайдер: YooKassa</div>
          <div>Сумма: 1 490 ₽</div>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmSoft}
        onCancel={() => setConfirmSoft(false)}
        onConfirm={() => setConfirmSoft(false)}
        title="Вернуть 1 490 ₽ пользователю @ivanov?"
        body="Деньги уйдут обратно на карту в течение 3 рабочих дней. Отменить возврат на стороне провайдера нельзя."
        confirmLabel="Вернуть 1 490 ₽"
        cancelLabel="Не возвращать"
      />

      <ConfirmDialog
        open={confirmHard}
        onCancel={() => setConfirmHard(false)}
        onConfirm={() => setConfirmHard(false)}
        title="Удалить клиента @ivanov?"
        body="Удаляются подписка, история платежей и ключи доступа. Восстановить нельзя."
        confirmLabel="Удалить клиента"
        cancelLabel="Оставить"
        destructive
        requireText="ivanov"
        requireHint="Введите имя пользователя без @, чтобы подтвердить"
      />
    </div>
  );
}

const SEMANTIC: Array<[string, string, string]> = [
  ["успех / оплачено", "bg-success-solid", "text-success"],
  ["ожидание", "bg-warning-solid", "text-warning"],
  ["риск / просрочено", "bg-risk-solid", "text-risk"],
  ["отказ / возврат", "bg-danger-solid", "text-danger"],
  ["информация", "bg-info-solid", "text-info"],
  ["деньги пришли", "bg-success-solid", "text-money-in"],
  ["деньги ушли", "bg-danger-solid", "text-money-out"],
];

const ROWS = [
  { id: "8f2a91c0", user: "@ivanov", plan: "3 месяца", status: "Оплачено", kind: "success", sum: "1 490 ₽", tone: "none" },
  { id: "1c04ab77", user: "@petrov", plan: "1 месяц", status: "В обработке", kind: "pending", sum: "590 ₽", tone: "none" },
  { id: "77de1103", user: "@sidorov", plan: null, status: "Просрочено", kind: "risk", sum: "590 ₽", tone: "risk" },
  { id: "b0093fa2", user: "@smirnov", plan: "1 год", status: "Возврат", kind: "failure", sum: "−4 470 ₽", tone: "failure" },
  { id: "44a1e9de", user: "@popov", plan: "1 месяц", status: "Оплачено", kind: "success", sum: "590 ₽", tone: "none" },
] as const;

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {note && <p className="mt-0.5 text-xs text-fg-subtle">{note}</p>}
      </div>
      {children}
    </section>
  );
}

/** Ступени шкалы подряд — так видно, что соседние отличимы, а крайние не
 *  сливаются с фоном. Классы перечислены целиком: Tailwind не понимает
 *  склеенные из переменных имена и вырезал бы их из сборки. */
function Ramp({ prefix, label }: { prefix: "bg-n-" | "bg-accent-"; label: string }) {
  const cells =
    prefix === "bg-n-"
      ? ["bg-n-1", "bg-n-2", "bg-n-3", "bg-n-4", "bg-n-5", "bg-n-6", "bg-n-7", "bg-n-8", "bg-n-9", "bg-n-10", "bg-n-11", "bg-n-12"]
      : [
          "bg-accent-1", "bg-accent-2", "bg-accent-3", "bg-accent-4", "bg-accent-5", "bg-accent-6",
          "bg-accent-7", "bg-accent-8", "bg-accent-9", "bg-accent-10", "bg-accent-11", "bg-accent-12",
        ];
  return (
    <div>
      <div className="mb-1 text-xs text-fg-subtle">{label}</div>
      <div className="flex overflow-hidden rounded-md border border-border">
        {cells.map((c, i) => (
          <div key={c} className={`h-8 flex-1 ${c}`} title={`${i + 1}`} />
        ))}
      </div>
    </div>
  );
}
