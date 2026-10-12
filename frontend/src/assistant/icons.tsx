import type { ReactNode, SVGProps } from 'react';

/** A small stroke icon set for plan elements; 16 px grid, currentColor. */
const paths: Record<string, ReactNode> = {
  spark: <><path d="M8 1.8 9.3 6.1 13.6 7.4 9.3 8.7 8 13 6.7 8.7 2.4 7.4 6.7 6.1Z" /><path d="M13 1.5v2.6M11.7 2.8h2.6" /></>,
  rule: <><rect x="3" y="7" width="10" height="7" rx="1.6" /><path d="M5.3 7V5a2.7 2.7 0 0 1 5.4 0v2" /><path d="M8 10v1.4" /></>,
  drone: <><circle cx="3.6" cy="3.6" r="2" /><circle cx="12.4" cy="3.6" r="2" /><circle cx="3.6" cy="12.4" r="2" /><circle cx="12.4" cy="12.4" r="2" /><path d="M5.2 5.2 6.6 6.6M10.8 5.2 9.4 6.6M5.2 10.8 6.6 9.4M10.8 10.8 9.4 9.4" /><rect x="6.3" y="6.3" width="3.4" height="3.4" rx=".8" /></>,
  kitchen: <><path d="M4.5 1.8v4.4a1.6 1.6 0 0 0 3.2 0V1.8M6.1 1.8v12.4" /><path d="M11.5 14.2V1.8c-1.7.8-2.4 2.6-2.4 4.6v2.4h2.4" /></>,
  customer: <><path d="M8 14.4s4.6-4.2 4.6-7.8A4.6 4.6 0 0 0 3.4 6.6c0 3.6 4.6 7.8 4.6 7.8Z" /><path d="m5.9 7 2.1-1.8L10.1 7v2H5.9Z" /></>,
  vertiport: <><circle cx="8" cy="8" r="6.2" /><path d="M5.8 4.9v6.2M10.2 4.9v6.2M5.8 8h4.4" /></>,
  handoff: <><path d="M2.5 5.5h9l-2.5-2.5" /><path d="M13.5 10.5h-9l2.5 2.5" /></>,
  incident: <><circle cx="9.8" cy="3.2" r="1.5" /><path d="M9.4 5.6 6.6 9.4l3.3 1.4-1.2 3.6M6.6 9.4 3.4 8.4M9.3 6.3l3.1 1.7" /><path d="M1.8 14.6h12.4" /></>,
  charging: <><path d="M9.2 1.6 3.8 9h4L6.8 14.4 12.2 7h-4Z" /></>,
  parcel: <><path d="m8 1.8 5.6 2.9v6.6L8 14.2l-5.6-2.9V4.7Z" /><path d="M2.4 4.7 8 7.6l5.6-2.9M8 7.6v6.6" /></>,
  meal: <><path d="M2 8.4h12a6 6 0 0 1-12 0Z" /><path d="M5.6 5.6c0-1 1-1.2 1-2.2M9.2 5.6c0-1 1-1.2 1-2.2" /></>,
  medical: <><rect x="2" y="2" width="12" height="12" rx="3" /><path d="M8 5v6M5 8h6" /></>,
  inspection: <><circle cx="7" cy="7" r="4.4" /><path d="m10.2 10.2 3.8 3.8" /></>,
  wind: <><path d="M1.8 6h8.4a2.2 2.2 0 1 0-2.2-2.2" /><path d="M1.8 10h10.4a2 2 0 1 1-2 2" /></>,
  clock: <><circle cx="8" cy="8" r="6.2" /><path d="M8 4.6V8l2.4 1.6" /></>,
  globe: <><circle cx="8" cy="8" r="6.2" /><path d="M1.8 8h12.4M8 1.8c1.8 1.8 2.6 3.9 2.6 6.2S9.8 12.4 8 14.2C6.2 12.4 5.4 10.3 5.4 8S6.2 3.6 8 1.8Z" /></>,
  check: <path d="m3.2 8.4 3 3 6.6-6.8" />,
  cross: <path d="m4.2 4.2 7.6 7.6M11.8 4.2l-7.6 7.6" />,
  dot: <circle cx="8" cy="8" r="2.4" />,
  play: <path d="M5 3.2v9.6L12.6 8Z" />,
  pause: <path d="M5.2 3.4v9.2M10.8 3.4v9.2" />,
  send: <path d="M2.4 8 13.6 2.6 10.4 13.4 7.6 9.2Z M7.6 9.2 13.6 2.6" />,
  stop: <rect x="4" y="4" width="8" height="8" rx="1.6" />,
  plug: <><path d="M5.6 1.8v3M10.4 1.8v3M3.8 4.8h8.4v2.6a4.2 4.2 0 0 1-8.4 0Z" /><path d="M8 11.6v2.6" /></>,
  layers: <><path d="m8 2 6 3.2-6 3.2-6-3.2Z" /><path d="m2 8.4 6 3.2 6-3.2M2 11.2l6 3.2 6-3.2" /></>,
  rewind: <><path d="M7.4 4 3 8l4.4 4" /><path d="M13 4 8.6 8l4.4 4" /></>,
  chat: <><path d="M2.4 3.6a1.6 1.6 0 0 1 1.6-1.6h8a1.6 1.6 0 0 1 1.6 1.6v5.6a1.6 1.6 0 0 1-1.6 1.6H6.4L3.2 13.6V10.8a1.6 1.6 0 0 1-.8-1.4Z" /></>,
  form: <><rect x="2.4" y="2" width="11.2" height="12" rx="1.6" /><path d="M5 5.4h6M5 8h6M5 10.6h3.6" /></>,
  eye: <><path d="M1.6 8s2.4-4.6 6.4-4.6S14.4 8 14.4 8s-2.4 4.6-6.4 4.6S1.6 8 1.6 8Z" /><circle cx="8" cy="8" r="2" /></>,
  branch: <><circle cx="4" cy="3" r="1.5" /><circle cx="4" cy="13" r="1.5" /><circle cx="12" cy="5.4" r="1.5" /><path d="M4 4.5v7M12 6.9c0 3-8 2.2-8 4.6" /></>,
  route: <><circle cx="3.4" cy="12.6" r="1.6" /><circle cx="12.6" cy="3.4" r="1.6" /><path d="M5 12.6h4.4a2.4 2.4 0 0 0 0-4.8H6.6a2.4 2.4 0 0 1 0-4.8H11" /></>,
};

/** Element kinds and roles to icons; unknown kinds fall back to a neutral dot. */
const aliases: Record<string, string> = {
  fleet: 'drone', vehicle: 'drone', meal_courier: 'drone', parcel_drone: 'drone', medical_drone: 'drone', inspection_drone: 'drone', patrol_drone: 'drone', air_taxi: 'drone', sprayer: 'drone',
  restaurant: 'kitchen', kitchen: 'kitchen', customer: 'customer', vertiport: 'vertiport', depot: 'vertiport', charging_pad: 'charging', chargingpad: 'charging',
  handoff_point: 'handoff', handoff: 'handoff', incident_site: 'incident', incident: 'incident', person: 'incident', person_fall: 'incident',
  parcel: 'parcel', food: 'meal', medical: 'medical', inspection: 'inspection', inspection_start: 'inspection', inspection_end: 'inspection', patrol: 'eye',
  passenger: 'drone', spraying: 'drone', wind: 'wind', gust: 'wind', workload: 'parcel', place: 'customer', event: 'clock', rule: 'rule', algorithm: 'layers',
  cooperation: 'handoff', system: 'layers', site: 'customer',
};

export function Icon({ name, size = 16, ...rest }: { name: string; size?: number } & SVGProps<SVGSVGElement>) {
  const key = paths[name] ? name : aliases[name] ?? aliases[name.toLowerCase()] ?? 'dot';
  return <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>{paths[key]}</svg>;
}
