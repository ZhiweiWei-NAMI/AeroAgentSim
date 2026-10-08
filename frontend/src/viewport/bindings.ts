import type { EntityKey, RunHeader, PresentationBinding } from '../contracts/viewer-feed';

export const entityId = (key: EntityKey): string => `${key.id}:${key.generation}`;
export function resolveBinding(header: RunHeader, typeId: string): PresentationBinding | undefined {
  const direct = header.presentation.find(binding => binding.typeId === typeId);
  if (direct) return direct;
  const type = header.types.find(type => type.typeId === typeId);
  for (const ancestor of type?.ancestors ?? []) {
    const binding = header.presentation.find(binding => binding.typeId === ancestor);
    if (binding) return binding;
  }
  return undefined;
}
