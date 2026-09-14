/**
 * Settings — appearance and brand on top, then the existing operational
 * settings (notifications, SBP routing, passkeys, push) unchanged.
 */
import { useBranding } from "@/lib/branding";
import { usePrefs, type Density, type MotionPref, type Theme } from "@/store/prefs";
import { Bento, PageHeader, SectionHeader, Surface } from "@/components/ui/Surface";
import { ListRow, Segmented } from "@/components/ui/controls";
import { PremiumRepairCard } from "@/components/PremiumRepairCard";
import { RemnawaveTagsCard } from "@/components/RemnawaveTagsCard";
import { Settings } from "./Settings";

const THEMES = [
  { value: "system" as Theme, label: "Как в системе" },
  { value: "light" as Theme, label: "Светлая" },
  { value: "dark" as Theme, label: "Тёмная" },
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
            <div className="flex flex-col gap-2">
              <span className="t-mute text-[13px]">Тема</span>
              <Segmented label="Тема" value={p.theme} options={THEMES} onChange={p.setTheme} full />
            </div>
            <div className="flex flex-col gap-2">
              <span className="t-mute text-[13px]">Плотность таблиц</span>
              <Segmented label="Плотность таблиц" value={p.density} options={DENSITIES} onChange={p.setDensity} full />
            </div>
            <div className="flex flex-col gap-2">
              <span className="t-mute text-[13px]">Анимация</span>
              <Segmented label="Анимация" value={p.motion} options={MOTIONS} onChange={p.setMotion} full />
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
                title="Цвет бренда"
                meta="BRAND_PRIMARY_COLOR · кнопки панели — системный синий"
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
      <SectionHeader
        title="Теги в панели Remnawave"
        sub="Тег тарифа у каждого пользователя с активной подпиской — фильтр «tag» в панели."
      />
      <Bento>
        <RemnawaveTagsCard className="sm:col-span-6 xl:col-span-12" />
      </Bento>
      <SectionHeader
        title="Премиум больше 5 лет"
        sub="Premium-сущности со сроком в панели дальше чем на 5 лет — дата по реальным покупкам."
      />
      <Bento>
        <PremiumRepairCard className="sm:col-span-6 xl:col-span-12" />
      </Bento>
    </>
  );
}
