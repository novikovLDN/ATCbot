import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarClock, Copy } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub, truncate } from "@/lib/format";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  StatTile,
} from "@/components/ui";
import { DeleteFromUsers } from "./DeleteFromUsers";
import { MessagePreview } from "./MessagePreview";
import { ScheduleModal } from "./ScheduleModal";
import { SendProgressBar } from "./SendProgressBar";
import type { SendProgress } from "./useSendProgress";
import { segmentLabel, useSegments } from "./useSegments";

/**
 * Карточка одной отправленной рассылки.
 *
 * ТРИ НЕЗАВИСИМЫХ ЗАПРОСА, И ОТКАЗ ОДНОГО НЕ ГАСИТ ОСТАЛЬНЫЕ: сама
 * рассылка, короткая статистика доставки и тяжёлая аналитика конверсии.
 * Последняя — это JOIN журнала рассылки с платежами, она заметно
 * медленнее двух первых, поэтому у неё свой интервал и своё место в
 * разметке. Раньше её отказ возвращал `null`, и блок просто исчезал:
 * человек не мог отличить «конверсии не было» от «мы не посчитали».
 *
 * ОТКУДА КАКОЕ ЧИСЛО. `/{id}/stats` отдаёт ровно два поля — sent и
 * failed. Общее число получателей есть только в `/{id}/analytics`.
 * Прежняя версия искала `total_recipients` в ответе `stats`, где его
 * никогда не было, и плитка «Получатели» показывала прочерк на каждой
 * рассылке.
 */

interface Detail {
  id?: number;
  title?: string;
  message?: string;
  /** В базе колонка называется `type`. Прежний экран читал
   *  `broadcast_type` — такого поля в ответе нет, и «Тип» всегда был
   *  прочерком. */
  type?: string;
  segment?: string;
  created_at?: string;
  is_ab_test?: boolean;
  photo_file_id?: string | null;
  animation_file_id?: string | null;
  buttons?: string[] | null;
  discount_percent?: number | null;
  discount_hours?: number | null;
  gift_reveal_percent?: number | null;
}

export function BroadcastDetail({
  id,
  deletable,
  progress,
  onClose,
}: {
  id: number;
  /** null — рассылки нет в загруженном списке, число неизвестно. */
  deletable: number | null;
  progress?: SendProgress;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [scheduling, setScheduling] = useState(false);
  const segments = useSegments();

  const detail = useQuery({
    queryKey: ["broadcasts", "detail", id],
    queryFn: () => endpoints.broadcastDetail(id) as Promise<Detail>,
  });

  const stats = useQuery({
    queryKey: ["broadcasts", "stats", id],
    queryFn: () => endpoints.broadcastStats(id) as Promise<{ sent?: number; failed?: number }>,
    // Пока рассылка идёт — раз в секунду: числа должны расти на глазах.
    refetchInterval: progress?.status === "running" ? 1_000 : 15_000,
  });

  const analytics = useQuery({
    queryKey: ["broadcasts", "analytics", id],
    queryFn: () => endpoints.broadcastAnalytics(id),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  if (detail.isError) {
    return (
      <Card className="p-4">
        <EmptyFailure
          what="карточку рассылки"
          reason={`Рассылка №${id} не открылась. Это отказ запроса — сама рассылка и её статистика на месте.`}
          onRetry={() => detail.refetch()}
        />
      </Card>
    );
  }

  const d = detail.data;

  return (
    <LoadingGate
      loading={detail.isLoading}
      skeleton={
        <Card className="space-y-3 p-4">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-3 w-32" />
          <div className="grid grid-cols-3 gap-2 pt-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
          <Skeleton className="h-24" />
        </Card>
      }
      message="Открываю рассылку"
    >
      {d && (
        <div className="space-y-3">
          <Card>
            <CardHeader
              title={truncate(String(d.title ?? "Без названия"), 70)}
              subtitle={`№${id} · ${fmtDate(d.created_at)} · кому: ${segmentLabel(
                segments.data,
                String(d.segment ?? ""),
              )}`}
              actions={
                <Button size="sm" onClick={onClose} className="lg:hidden">
                  Закрыть
                </Button>
              }
            />

            <CardBody className="space-y-3">
              {progress && <SendProgressBar progress={progress} />}

              {/* Что можно сделать с уже отправленной. «Повторить» и
                  «Отложить» безобидны — они лишь открывают форму, поэтому
                  подтверждения у них нет. */}
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  icon={<Copy className="h-3.5 w-3.5" />}
                  onClick={() => navigate(`/broadcasts/new?clone=${id}`)}
                  title="Открыть мастер с этим текстом, медиа и кнопками. Аудиторию выбираете заново"
                >
                  Повторить
                </Button>
                <Button
                  size="sm"
                  icon={<CalendarClock className="h-3.5 w-3.5" />}
                  onClick={() => setScheduling(true)}
                  title="Отправить копию этой рассылки в назначенное время"
                >
                  Отложить
                </Button>
              </div>

              <div className="flex flex-col gap-2">
                <DeleteFromUsers broadcastId={id} deletable={deletable} />
              </div>
            </CardBody>
          </Card>

          <DeliveryStats
            sent={stats.data?.sent}
            failed={stats.data?.failed}
            total={analytics.data?.total_recipients}
            totalUnknown={analytics.isError}
          />

          <Conversion
            loading={analytics.isLoading}
            error={analytics.isError}
            data={analytics.data}
            onRetry={() => analytics.refetch()}
          />

          <Card>
            <CardHeader
              title="Что получили люди"
              subtitle="так это выглядело в Telegram"
            />
            <CardBody>
              {d.message ? (
                <MessagePreview
                  message={String(d.message)}
                  buttons={Array.isArray(d.buttons) ? d.buttons.map(String) : []}
                  photo={Boolean(d.photo_file_id)}
                  animation={Boolean(d.animation_file_id)}
                />
              ) : (
                <div className="text-base text-fg-muted">Текст не сохранился.</div>
              )}

              <dl className="mt-3 space-y-1 border-t border-border-subtle pt-3 text-base">
                <Fact label="Тип" value={String(d.type ?? "обычная")} />
                <Fact label="A/B-тест" value={d.is_ab_test ? "да" : "нет"} />
                {d.discount_percent != null && (
                  <Fact
                    label="Скидка по кнопке"
                    value={`${d.discount_percent}%${
                      d.discount_hours ? ` на ${d.discount_hours} ч` : ""
                    }`}
                  />
                )}
                {d.gift_reveal_percent != null && (
                  <Fact
                    label="Скидка за «подарок»"
                    value={`${d.gift_reveal_percent}% на 48 ч`}
                  />
                )}
              </dl>
            </CardBody>
          </Card>

          <ScheduleModal
            broadcastId={id}
            open={scheduling}
            onClose={() => setScheduling(false)}
          />
        </div>
      )}
    </LoadingGate>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-medium text-fg">{value}</dd>
    </div>
  );
}

/** Доставка: сколько ушло, сколько не дошло, скольким предназначалась. */
function DeliveryStats({
  sent,
  failed,
  total,
  totalUnknown,
}: {
  sent?: number;
  failed?: number;
  total?: number;
  totalUnknown: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
      <StatTile
        label="Получателей"
        value={total == null ? "—" : fmtNum(total)}
        hint={
          total == null
            ? totalUnknown
              ? "не посчитали"
              : "считаем"
            : "кому предназначалась"
        }
      />
      <StatTile
        label="Дошло"
        value={fmtNum(sent ?? 0)}
        hint={
          total ? `${Math.round(((sent ?? 0) / total) * 100)}% от всех` : "доставлено"
        }
      />
      <StatTile
        label="Не дошло"
        value={fmtNum(failed ?? 0)}
        hint="заблокировали бота или удалили аккаунт"
      />
    </div>
  );
}

/** Конверсия и деньги: кто купил после рассылки и на сколько. */
function Conversion({
  loading,
  error,
  data,
  onRetry,
}: {
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  data?: {
    delivered: number;
    converted_1d: number;
    converted_3d: number;
    converted_7d: number;
    revenue_kop_1d: number;
    revenue_kop_3d: number;
    revenue_kop_7d: number;
    conversion_rate_7d: number;
    blocked_estimate: number;
    deleted: number;
  };
}) {
  if (error) {
    return (
      <Card className="p-4">
        {/* Раньше здесь был return null. Исчезнувший блок неотличим от
            «конверсии не случилось» — а это противоположные новости. */}
        <EmptyFailure
          what="конверсию рассылки"
          reason="Подсчёт покупок после рассылки не ответил. Сколько людей купило — сейчас неизвестно."
          onRetry={onRetry}
        />
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Кто купил после рассылки"
        subtitle="покупки в первые сутки, трое суток и неделю после доставки"
      />
      <CardBody>
        <LoadingGate
          loading={loading}
          skeleton={
            <div className="grid grid-cols-3 gap-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          }
          message="Считаю покупки после рассылки"
        >
          {data && (
            <>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Window label="За сутки" count={data.converted_1d} kop={data.revenue_kop_1d} />
                <Window label="За трое суток" count={data.converted_3d} kop={data.revenue_kop_3d} />
                <Window label="За неделю" count={data.converted_7d} kop={data.revenue_kop_7d} />
              </div>

              <dl className="mt-3 space-y-1 border-t border-border-subtle pt-3 text-base">
                <Fact
                  label="Купили за неделю"
                  value={`${(data.conversion_rate_7d * 100).toFixed(2)}% от дошедших`}
                />
                <Fact
                  label="Заблокировали бота, оценка"
                  value={`${(data.blocked_estimate * 100).toFixed(1)}%`}
                />
                <Fact label="Удалено из чатов" value={fmtNum(data.deleted)} />
              </dl>
            </>
          )}
        </LoadingGate>
      </CardBody>
    </Card>
  );
}

function Window({ label, count, kop }: { label: string; count: number; kop: number }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-xs text-fg-subtle">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums text-fg">
        {fmtNum(count)}
      </div>
      <div className="mt-0.5 text-xs tabular-nums text-fg-muted">{fmtRub(kop / 100)}</div>
    </div>
  );
}
