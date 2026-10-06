# P02 actual configuration round-trip

The Run Configuration control now opens the editable City Studio. These captures verify editing seed 20261002 to 20261003, Save draft, reload with the edited value retained, Chinese/English switching, independent panel scrolling, and return to the exact original sealed-v8 replay URL. The approved 20261002 draft was restored before the existing matching compilation was loaded. No new native execution was started.

![Actual edit, save and reload](01-edit-save-reload.gif)

Actual browser frames: open Studio, change seed, save, reload. Dimensions remain 1600 × 1000; GIF palette is reduced to 128 colors. Original PNGs are retained.

![Actual language switch, compiled handoff and replay return](02-language-compile-return.gif)

Actual browser frames: switch language, display the matching compiled artifact, return to the existing sealed-v8 replay. This GIF does not show a new simulation run.

## Evidence

- [English editor at 1200 × 800](08-studio-narrow-en.png)
- [Chinese editor at 1600 × 1000](06-studio-editor-zh.png)
- [Edited value after reload](05-edited-draft-reloaded.png)
- [Compiled handoff](09-matching-compiled-handoff.png)
- [Original replay restored](10-original-replay-restored.png)
- [Operation assertions](operation-evidence.json) and [source/artifact hashes](manifest.json)

The empty right selection panel is collapsed in Studio, while a selected entity panel remains available. Both tested viewports have no body overflow; the editor panel scrolls internally. The source badge and Not run status remain visible. The original BENCH city/GLB scene is retained.

Validation: 49 focused frontend tests, TypeScript checking and the production build passed before capture. The browser assertions pass for editable fields, saved-value retention, language switching, internal panel scrolling and exact replay return. Pixel review still belongs to the independent reviewer.

## Native execution boundary

Compilation a504dba5ab6c8c2183b5ce419173f89bd03289860e62ffeee760ff529417c610 matches saved draft 68c049b28e15d4e2886b36ff222361e6bfb059b4ff8b23339dd7e0a4fa7f368f. ControlRunManager readback consumed the compiled seed, but this is not proof of a started simulator. The currently served sealed-v8 replay is a different run. The isolated supported v7 launch needs the existing authorized operator context: AERO_BENCH_CONTROL_BOOTSTRAP_TOKEN and AERO_BENCH_CONTROL_BOOTSTRAP_CSRF. Neither was present in the coordinator launch context. Authentication remains enabled, no credentials were created, and the active v8 service was not restarted. Full configuration-to-new-native-execution GIF acceptance remains pending that context.
