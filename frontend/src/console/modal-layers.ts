const layers: HTMLElement[] = [];

/** Mounted dialogs own keyboard input in their opening order. */
export function registerModalLayer(dialog: HTMLElement) {
  layers.push(dialog);
  return () => { const index = layers.indexOf(dialog); if (index !== -1) layers.splice(index, 1); };
}

export function isTopModalLayer(dialog: HTMLElement | null) {
  return !!dialog && layers.filter(layer => layer.isConnected).at(-1) === dialog;
}
