import { CITY_WEATHER_CLEAR, CITY_WEATHER_PRESETS, checkCityWeatherSettings, type CityWeatherSettings } from "./city-weather";

/** Visual preview controls on the render bar. They never produce Weather Provider
 * samples; with an applied workspace draft they edit the draft environment instead. */
export function createRenderWeatherControls(onChange: (settings: CityWeatherSettings) => void): {
  element: HTMLElement; sync: (settings: CityWeatherSettings) => void;
} {
  let current: CityWeatherSettings = { ...CITY_WEATHER_CLEAR };
  const element = document.createElement("div");
  element.className = "city-preview-row city-render-weather";
  element.setAttribute("aria-label", "视觉天气预览（不保存为仿真配置）");
  const preset = document.createElement("select");
  preset.name = "render-weather-preset";
  preset.setAttribute("aria-label", "视觉天气预设");
  const custom = new Option("自定义", ""); custom.disabled = true;
  preset.append(custom, ...([['clear', '晴'], ['cloudy', '多云'], ['rain', '雨'], ['fog', '雾'], ['snow', '雪']] as const)
    .map(([value, label]) => new Option(label, value)));
  element.append(preset);
  const input = (name: string, label: string, max: string | null): HTMLInputElement => {
    const wrapper = document.createElement("label"); wrapper.textContent = label;
    const field = document.createElement("input");
    field.type = "number"; field.name = name; field.min = "0"; field.step = "any"; field.required = true;
    if (max !== null) field.max = max;
    wrapper.append(field); element.append(wrapper);
    return field;
  };
  const speed = input("render-wind-speed", "风速 m/s ", null);
  const direction = input("render-wind-direction", "风向 ° ", "359.99999999999994");
  const sync = (settings: CityWeatherSettings): void => {
    checkCityWeatherSettings(settings);
    current = { ...settings };
    preset.value = Object.entries(CITY_WEATHER_PRESETS).find(([, value]) =>
      Object.entries(value).every(([key, entry]) => current[key as keyof CityWeatherSettings] === entry))?.[0] ?? "";
    speed.value = String(settings.windMps); direction.value = String(settings.windDirectionDeg);
  };
  preset.addEventListener("change", () => {
    const selected = CITY_WEATHER_PRESETS[preset.value as keyof typeof CITY_WEATHER_PRESETS];
    if (selected === undefined) throw new Error(`Unknown visual weather preset: ${preset.value}`);
    const next = { ...selected }; onChange(next); sync(next);
  });
  for (const [field, key] of [[speed, "windMps"], [direction, "windDirectionDeg"]] as const) {
    field.addEventListener("change", () => {
      if (!field.reportValidity()) return;
      const next = { ...current, [key]: field.valueAsNumber };
      checkCityWeatherSettings(next); onChange(next); sync(next);
    });
  }
  const note = document.createElement("span");
  note.dataset.role = "render-weather-note";
  note.textContent = "未保存的视觉预览";
  note.dataset.mode = "preview";
  element.append(note); sync(current);
  return { element, sync };
}
