import type { CitySelectedScenario } from "./city-selected-scenario";
import type { LogisticsOrderGeneration, LogisticsOrderRequest } from "./city-selected-logistics-draft";

/** Deterministic demand input for one selected city. This does not create business state. */
export type SelectedOrderGeneration = LogisticsOrderGeneration;
export type GeneratedOrderRequest = LogisticsOrderRequest;

const MAX_GENERATED_ORDERS = 10_000;

function finite(value: number, name: string): void {
  if (!Number.isFinite(value)) throw new Error(`${name} 必须为有限数值`);
}

function validate(config: SelectedOrderGeneration): void {
  for (const [key, value] of Object.entries(config)) finite(value, key);
  if (!Number.isSafeInteger(config.seed) || config.seed < 0) throw new Error("seed 必须为非负安全整数");
  if (!Number.isSafeInteger(config.maxOrders) || config.maxOrders < 0
      || config.maxOrders > MAX_GENERATED_ORDERS) throw new Error(`maxOrders 必须在 0 至 ${MAX_GENERATED_ORDERS} 之间`);
  if (config.startAtS < 0 || config.endAtS <= config.startAtS) throw new Error("订单生成时间范围无效");
  if (config.cargoMinKg <= 0 || config.cargoMaxKg < config.cargoMinKg) throw new Error("订单货重范围无效");
  if (config.deadlineLeadS <= 0) throw new Error("订单时限必须大于 0 秒");
}

/** A 32-bit stream with both halves of a safe-integer seed mixed into its initial state. */
function randomStream(seed: number): () => number {
  const low = seed >>> 0;
  const high = Math.floor(seed / 0x1_0000_0000) >>> 0;
  let state = (low ^ Math.imul(high, 0x9e3779b9) ^ 0xa5a5a5a5) >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 0x1_0000_0000;
  };
}

/** Build release-time requests; a separate Business Provider must accept and execute them. */
export function generateSelectedOrderRequests(
  scenario: CitySelectedScenario,
  config: SelectedOrderGeneration,
): GeneratedOrderRequest[] {
  validate(config);
  if (config.maxOrders === 0) return [];
  const facilities = scenario.facilities.filter(item => item.kind === "vertiport" || item.kind === "hub")
    .map(item => item.id).sort();
  if (facilities.length < 2) throw new Error("至少需要两处可装卸设施才能生成订单");
  const hubs = scenario.facilities.filter(item => item.kind === "hub").map(item => item.id).sort();
  if (hubs.length === 0) throw new Error("订单必须经过物流中转站交接，当前场景没有物流中转站");
  if (!scenario.fleet.some(item => item.count > 0 && item.maxPayloadKg >= config.cargoMaxKg)) {
    throw new Error("订单最大货重超过当前机队的载重能力");
  }
  const random = randomStream(config.seed);
  const requests: GeneratedOrderRequest[] = [];
  for (let index = 0; index < config.maxOrders; index++) {
    const sourceIndex = Math.floor(random() * facilities.length);
    const destinationOffset = 1 + Math.floor(random() * (facilities.length - 1));
    const sourceFacilityId = facilities[sourceIndex]!;
    const destinationFacilityId = facilities[(sourceIndex + destinationOffset) % facilities.length]!;
    const isHub = (id: string): boolean => hubs.includes(id);
    let hubHandoffFacilityId: string | null = null;
    if (!isHub(sourceFacilityId) && !isHub(destinationFacilityId)) {
      const eligible = hubs.filter(id => id !== sourceFacilityId && id !== destinationFacilityId);
      hubHandoffFacilityId = eligible[Math.floor(random() * eligible.length)]!;
    }
    const releaseAtS = config.startAtS + (index + random())
      * (config.endAtS - config.startAtS) / config.maxOrders;
    const cargoKg = config.cargoMinKg + random() * (config.cargoMaxKg - config.cargoMinKg);
    const deliverByS = releaseAtS + config.deadlineLeadS;
    if (!Number.isFinite(releaseAtS) || !Number.isFinite(cargoKg) || !Number.isFinite(deliverByS)
        || releaseAtS >= config.endAtS || deliverByS <= releaseAtS) {
      throw new Error("订单生成数值超出可表示范围");
    }
    requests.push({
      id: `generated-${config.seed}-${index + 1}`,
      sourceFacilityId, destinationFacilityId, hubHandoffFacilityId, cargoKg, releaseAtS, deliverByS,
    });
  }
  return requests;
}
