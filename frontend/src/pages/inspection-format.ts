import type { EntityState } from '../viewport/feed-store';
import type { FieldInfo } from '../contracts/viewer-feed';
import { translate } from '../i18n/translate';

/** Localize known authored labels, not arbitrary user names or retained identities. */
export function localizedLabel(value: string, locale = 'en-US'): string {
  if (!locale.startsWith('zh')) return value;
  let match: RegExpMatchArray | null;
  if ((match = value.match(/^(Parcel|Medical|Passenger|Spraying|Inspection|Patrol|Incident Response) mission (\d+)$/i))) {
    return translate(locale,'{kind} mission {number}', {kind:readableLabel(match[1],locale),number:match[2]});
  }
  if ((match = value.match(/^(Assessment|Bid) (\d+) (alpha|bravo)$/i))) return translate(locale,'{kind} {number} {name}',{kind:readableLabel(match[1],locale),number:match[2],name:readableLabel(match[3],locale)});
  if ((match = value.match(/^Report (\d+)$/i))) return translate(locale,'Report {number}',{number:match[1]});
  if ((match = value.match(/^Route vehicle (\d+)$/i))) return translate(locale,'Route vehicle {number}',{number:match[1]});
  if ((match = value.match(/^Road vehicle (\d+|reporter|a|b)$/i))) return translate(locale,'Road vehicle {name}',{name:readableLabel(match[1],locale)});
  if ((match = value.match(/^Road task vehicle (.+)$/i))) return translate(locale,'Road task vehicle {name}',{name:readableLabel(match[1],locale)});
  if ((match = value.match(/^Incident drone (Alpha|Bravo|Bg \d+)$/i))) return translate(locale,'Incident drone {name}',{name:readableLabel(match[1],locale)});
  if ((match = value.match(/^Route uav (Alpha|Bravo|Bg \d+)$/i))) return translate(locale,'Route UAV {name}',{name:readableLabel(match[1],locale)});
  if ((match = value.match(/^(Background|District) patrol (\d+)$/i))) return match[1].toLowerCase()==='background' ? translate(locale,'Background patrol {number}',{number:match[2]}) : translate(locale,'District patrol {number}',{number:match[2]});
  if ((match = value.match(/^Medical delivery (\d+)$/i))) return translate(locale,'Medical delivery {number}',{number:match[1]});
  if ((match = value.match(/^Meal courier (\d+)$/i))) return translate(locale,'Meal courier {number}',{number:match[1]});
  if ((match = value.match(/^Meal customer (\d+)$/i))) return translate(locale,'Meal customer {number}',{number:match[1]});
  if ((match = value.match(/^Meal order (\d+)$/i))) return translate(locale,'Meal order {number}',{number:match[1]});
  if ((match = value.match(/^Incident capture (\d+)$/i))) return translate(locale,'Incident capture {number}',{number:match[1]});
  if ((match = value.match(/^Incident (\d+)$/i))) return translate(locale,'Incident {number}',{number:match[1]});
  if ((match = value.match(/^Air taxi (\d+)$/i))) return translate(locale,'Air taxi {number}',{number:match[1]});
  if ((match = value.match(/^(Drone|UAV) ([A-Za-z]*\d+)$/i))) return translate(locale,'Drone {number}',{number:match[2]});
  if ((match = value.match(/^Customer rooftop (\d+)$/i))) return translate(locale,'Customer rooftop {number}',{number:match[1]});
  if ((match = value.match(/^Survey drone (\d+)$/i))) return translate(locale,'Survey drone {number}',{number:match[1]});
  if ((match = value.match(/^Patrol drone (\d+)$/i))) return translate(locale,'Patrol drone {number}',{number:match[1]});
  if ((match = value.match(/^Sprayer (\d+)$/i))) return translate(locale,'Sprayer {number}',{number:match[1]});
  if ((match = value.match(/^Customer (\d+)$/i))) return translate(locale,'Customer {number}',{number:match[1]});
  if ((match = value.match(/^Vertiport (\d+)$/i))) return translate(locale,'Vertiport {number}',{number:match[1]});
  if ((match = value.match(/^Charging pad (\d+)$/i))) return translate(locale,'Charging pad {number}',{number:match[1]});
  if ((match = value.match(/^Depot (\d+)$/i))) return translate(locale,'Depot {number}',{number:match[1]});
  if ((match = value.match(/^(Vehicle|Car) (\d+)$/i))) return translate(locale,'Vehicle {number}',{number:match[2]});
  if ((match = value.match(/^Task (\d+)$/i))) return translate(locale,'Task {number}',{number:match[1]});
  if ((match = value.match(/^Bg (\d+)$/i))) return translate(locale,'Background {number}',{number:match[1]});
  if ((match = value.match(/^Incident ([AB])$/i))) return translate(locale,'Incident {name}',{name:match[1]});
  return translate(locale,value);
}

/** Display labels only; retained identities and recorded values stay intact. */
export function readableLabel(value: string, locale = 'en-US'): string {
  const words = value.slice(value.lastIndexOf(':')+1).replace(/^traffic\.p\./, '').replace(/^traffic\./, '').replace(/^lowalt\.(?:p\.)?/, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[._/-]+/g, ' ').trim();
  const label = words ? words.replace(/\b\w/g, letter => letter.toUpperCase()).replace(/\bUav\b/g, 'UAV').replace(/\bLlm\b/g, 'LLM').replace(/\bPx4\b/g, 'PX4') : value;
  return localizedLabel(label, locale);
}
export function displayLabel(label: string | undefined, id: string, locale = 'en-US'): string {
  // Slashes inside an English unit/axis label are not bilingual separators.
  const parts = label ? /[\u3400-\u9fff]/.test(label) ? label.split('/').map(part => part.trim()) : [label.trim()] : undefined;
  if (locale.startsWith('zh')) {
    const chinese = parts?.find(part => /[\u3400-\u9fff]/.test(part));
    if (chinese && chinese !== id) return chinese;
  }
  const english = parts?.find(part => /[a-z]/i.test(part) && !/[\u3400-\u9fff]/.test(part));
  if (english && english !== id) return localizedLabel(english, locale);
  const field = id.replace(/^(?:he\.aircraft|traffic\.(?:uav|actor))\./, '');
  const labels: Record<string, string> = {position_enu_m:'Position (east / north / up)', velocity_enu_mps:'Velocity (east / north / up)', energy_j:'Energy', reserve_j:'Energy reserve', max_speed_mps:'Max speed', max_accel_mps2:'Max acceleration'};
  return Object.prototype.hasOwnProperty.call(labels, field) ? translate(locale, labels[field]) : readableLabel(field, locale);
}
export function displayUnit(field: FieldInfo | undefined, locale = 'en-US'): string | undefined {
  const label = (unit: string) => unit === '1' ? undefined : translate(locale, unit);
  if (field?.unit) return label(field.unit);
  const schema = field?.schema;
  if (!schema || typeof schema !== 'object') return;
  const unit = (schema as {unit?:unknown}).unit;
  if (typeof unit === 'string') return label(unit);
  if (unit && typeof unit === 'object' && 'symbol' in unit && typeof unit.symbol === 'string') return label(unit.symbol);
}
export function entityLabel(entity: EntityState, locale = 'en-US'): string {
  for (const id of ['traffic.actor.label', 'oo:name', 'lowalt.label', 'name', 'label', 'display_name']) {
    const chinese = locale.startsWith('zh') ? entity.fields.get(`${id}_zh`)?.value : undefined;
    if (typeof chinese === 'string' && chinese.trim()) return chinese;
    const value = entity.fields.get(id)?.value;
    if (typeof value === 'string' && value.trim()) return localizedLabel(value, locale);
  }
  const authored = locale.startsWith('zh') ? entity.authoredLabel?.label_zh ?? entity.authoredLabel?.label : entity.authoredLabel?.label;
  if (authored?.trim()) return localizedLabel(authored, locale);
  return readableLabel(entity.key.id, locale);
}
export function stateValue(value: unknown, locale = 'en-US'): string | undefined {
  if (value === null) return translate(locale, 'Null (recorded)');
  if (typeof value === 'string') return localizedLabel(value, locale);
  if (typeof value === 'boolean') return translate(locale, String(value));
  const number = (item: unknown): string | undefined => {
    const tagged = item !== null && typeof item === 'object' && '$number' in item;
    const raw = tagged ? (item as {$number:unknown}).$number : item;
    const numeric = typeof raw === 'number' ? raw : tagged && typeof raw === 'string' && raw.trim() ? Number(raw) : undefined;
    return numeric !== undefined && Number.isFinite(numeric) ? new Intl.NumberFormat(locale, {maximumFractionDigits:2}).format(numeric) : undefined;
  };
  if (typeof value === 'number' || value && typeof value === 'object' && '$number' in value) return number(value);
  if (Array.isArray(value) && value.every(item => number(item) !== undefined)) return value.map(number).join(' · ');
  return undefined;
}
