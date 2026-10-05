export function parseJsonObjectBytes(bytes: Uint8Array, options?: {
  parseValue?: (text: string, depthOffset: number) => unknown;
  keys?: ReadonlySet<string>;
  onArrayItem?: (key: string, value: unknown, index: number) => void;
}): Record<string, unknown>;
