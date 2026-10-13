`PlacePickerMap` is the shared geographic picker for AddPlace and AddObjects. Its exported props in `PlacePickerMap.tsx` are the contract. It reads the C9 catalog, uses the full applied workspace's bounds/origin/compiled geometry, and shows current scenario places and fleet starts. It never creates or updates objects.

In `objects/AddPlace.tsx`, replace the origin-only `RegionMap`, null basemap, and separate `pickMarker` state. Import `PlacePickerMap` and `canPickOnMap` from `../space/PlacePickerMap`. Set `canPick = canPickOnMap(project_detail ?? project)`. Parse the live east/north fields using the form's existing `num` helper and mount:

```tsx
const pickerEast = num(east), pickerNorth = num(north);

{picking && <PlacePickerMap
  api={api}
  workspace={project_detail ?? project}
  draft={draft}
  L={L}
  position={pickerEast === undefined || pickerNorth === undefined
    ? null : [pickerEast, pickerNorth]}
  onPick={(pickedEast, pickedNorth) => {
    setEast(pickedEast.toFixed(1));
    setNorth(pickedNorth.toFixed(1));
    setMode('enu');
    touch();
  }}
/>}
```

Keep the picker mounted while `picking` is true, including when mode is `enu`. Opening it should select the coordinate-entry mode and show the east/north/up fields. A map click changes only east/north; preserve the authored up value. `touch()` invalidates the place review. Do not submit a `map` position mode without actual coordinates. Typed east/north edits also invalidate review and immediately move the controlled marker; empty/invalid fields pass `null` and remove the marker. Object changes continue through the caller's C11 preview/apply flow.

Use the same mount in `objects/AddObjects.tsx`, without AddPlace's `setMode`/`touch` calls. Its existing east/north setters update the creation review. Use `canPickOnMap` for gating and retain its inline region prerequisite. `onOpenRegion` is an optional callback for a “Select region” button if the caller can navigate there.
