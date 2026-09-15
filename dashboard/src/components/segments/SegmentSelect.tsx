/**
 * Compact segment picker (a <select>) for the schedule dialog and the
 * notification filter. A parametric segment reveals the period picker; the
 * value is always a full key ("paid_ended:6m") or "" (none).
 */
import type { BroadcastSegment } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { defaultKeyOf, splitSegmentKey } from "@/lib/segments";
import { SegmentWindowPicker } from "@/components/segments/SegmentWindowPicker";

export function SegmentSelect({
  segments,
  loading,
  value,
  onChange,
  emptyLabel,
  ariaLabel,
}: {
  segments: BroadcastSegment[] | undefined;
  loading?: boolean;
  value: string;
  onChange: (key: string) => void;
  emptyLabel: string;
  ariaLabel: string;
}) {
  const { base } = splitSegmentKey(value);
  const spec = segments?.find((s) => s.key === base);
  // A stored key the list no longer has (a removed segment) stays visible.
  const orphan = Boolean(value) && !loading && !spec;
  return (
    <div className="flex flex-col gap-2">
      <select
        value={orphan ? value : base}
        onChange={(e) => {
          const picked = segments?.find((s) => s.key === e.target.value);
          onChange(picked ? defaultKeyOf(picked) : e.target.value);
        }}
        className="input"
        aria-label={ariaLabel}
        disabled={loading}
      >
        <option value="">{loading ? "Загружаю сегменты…" : emptyLabel}</option>
        {orphan && <option value={value}>{value} (нет в списке)</option>}
        {(segments ?? []).map((s) => (
          <option key={s.key} value={s.key}>
            {s.group ? `[${s.group}] ` : ""}
            {s.label}
            {s.parametric ? " · выбрать период" : ` · ${fmtNum(s.count)} чел`}
          </option>
        ))}
      </select>
      {spec?.parametric && <SegmentWindowPicker spec={spec} value={value} onChange={onChange} />}
    </div>
  );
}
