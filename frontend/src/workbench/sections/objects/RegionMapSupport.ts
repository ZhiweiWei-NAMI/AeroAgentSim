/** Shared support for the region map: the ObjectsPanel fallback palette and
 *  instance-dot colouring, kept in one place so both sections agree on colours. */

/** The ObjectsPanel.tsx type-colour palette, mirrored here (spec 5 fallback palette). */
export const FALLBACK_PALETTE = ['#1f7ae0', '#e8a317', '#1fae6b', '#b26bff', '#e0453a', '#38c6f4', '#ff7a45', '#5b6b7d'];

/** Resolve an instance's colour from the draft presentation binding, else the palette fallback. */
export const paletteColorFor = (typeId: string, typeIndex: Map<string, number>): string => {
  const index = typeIndex.get(typeId);
  return index === undefined ? FALLBACK_PALETTE[0] : FALLBACK_PALETTE[index % FALLBACK_PALETTE.length];
};
