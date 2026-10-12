# Workbench media

These recordings show the single 3D workbench in English at 1280 × 800.
GIFs retain 15–40 seconds of productive browser footage; marked backend waits
are omitted. Selection, commands, receipts, causal links and photographs all
come from real runs. Captions and cursor highlights are recording overlays.

| Recording | Story |
| --- | --- |
| [00-overview.gif](00-overview.gif) | Gallery → guide → create project → configure → validate → save version → initialize at t = 0 → Start; hovering panels at a glance |
| [01-configure.gif](01-configure.gif) | Controls, the seven configuration sections, a seed validation issue jumping to its field, and YAML |
| [02-run-and-operate.gif](02-run-and-operate.gif) | Simulation speed; linked drone selection in the list, 3D and graph; inspector; restaurant picker, delay and admission receipt |
| [03-why.gif](03-why.gif) | Recorded timeline and Why? links from restaurant delay to preparation ready, task assignment and delivery |
| [04-photos.gif](04-photos.gif) | Shipped scheduled accident, response trace through dispatch, and the real stored UAV oblique photograph |
| [05-records-compare source](../../frontend/e2e/flows/05-records-compare.spec.ts) | Recording blocked: Records replay lacks the required history banner and Back to live. The failing browser assertion is retained; no substitute GIF is published. |

The 1600 × 1000 screenshots show scheduled runs in the running workbench with the installed city:
[food delivery with a selected drone (English)](workbench-food.png),
[food delivery with a selected drone (中文)](workbench-food-zh.png), and
[city-day with the relation graph open](workbench-city.png).
The food operation story explicitly selects Interactive mode and 80-second
preparation at the east kitchen; the baseline overview uses the shipped defaults.
Comparison therefore reflects real configuration and runtime differences.

Reproduce with [the recording tool](../../tools/docs/README.md).
