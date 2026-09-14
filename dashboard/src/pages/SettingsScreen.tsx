/**
 * Settings — appearance and brand on top, then the existing operational
 * settings (notifications, SBP routing, passkeys, push) unchanged.
 */
import { useBranding } from "@/lib/branding";
import { usePrefs, type Density, type MotionPref, type Theme } from "@/store/prefs";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { ListRow, Segmented } from "@/components/ui/controls";
import { Settings } from "./Settings";

const THEMES = [
  { value: "dark" as Theme, label: "Тёмные плитки" },
  { value: "light" as Theme, label: "Светлые плитки" },
];
const DENSITIES = [
  { value: "compact" as Density, label: "Плотно" },
  { value: "comfortable" as Density, label: "Обычно" },
  { value: "spacious" as Density, label: "Свободно" },
];
const MOTIONS = [
  { value: "system" as MotionPref, label: "Как в системе" },
  { value: "reduced" as MotionPref, label: "Без анимации" },
];

export function SettingsScreen() {
  const p = usePrefs();
  const brand = useBranding();
  return (
    <>
      <PageHeader title="Настройки" sub="Оформление хранится в этом браузере. Бренд задаётся переменными окружения." />
      <Bento>
        <Surface className="sm:col-span-6 xl:col-span-7" label="Оформление">
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[14px]">Тема</span>
              <Segmented label="Тема" value={p.theme} options={THEMES} onChange={p.setTheme} />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[14px]">Плотность таблиц</span>
              <Segmented label="Плотность таблиц" value={p.density} options={DENSITIES} onChange={p.setDensity} />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[14px]">Анимация</span>
              <Segmented label="Анимация" value={p.motion} options={MOTIONS} onChange={p.setMotion} />
            </div>
          </div>
        </Surface>
        <Surface className="sm:col-span-6 xl:col-span-5" variant="raised" label="Бренд">
          <ul className="flex flex-col gap-2">
            <li>
              <ListRow title="Название" meta="BRAND_NAME, BRAND_SHORT" value={brand.name} />
            </li>
            <li>
              <ListRow
                title="Акцентный цвет"
                meta="BRAND_PRIMARY_COLOR"
                value={brand.primary_color}
                trailing={<span className="h-8 w-8 flex-none rounded-full" style={{ background: brand.primary_color }} aria-hidden="true" />}
              />
            </li>
            <li>
              <ListRow title="Логотип" meta="BRAND_LOGO_URL" value={brand.logo_url ? "задан" : "по умолчанию"} />
            </li>
            {brand.support_url && (
              <li>
                <ListRow title="Поддержка" meta="BRAND_SUPPORT_URL" value={brand.support_url.replace("https://", "")} />
              </li>
            )}
          </ul>
        </Surface>
      </Bento>
      <Settings />
    </>
  );
}
