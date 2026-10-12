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
| [05-records-compare.gif](05-records-compare.gif) | Seek into a live run → Viewing history → Back to live; Records → replay a completed run → compare two runs → Modify & retry |

The 1600 × 1000 screenshots show scheduled runs in the running workbench with the installed city:
[food delivery with a selected drone (English)](workbench-food.png),
[food delivery with a selected drone (中文)](workbench-food-zh.png), and
[city-day with the relation graph open](workbench-city.png).
The food operation story explicitly selects Interactive mode and 80-second
preparation at the east kitchen; the baseline overview uses the shipped defaults.
Comparison therefore reflects real configuration and runtime differences.
The history banner and Back to live belong to a live run viewed at a past
moment. Completed runs use the separate replay controls; story 05 shows both.

Reproduce with [the recording tool](../../tools/docs/README.md). The published
GIFs fit the 25 MB budget with:

```sh
tools/docs/record-docs-media.sh --python .venv/bin/python \
  --work-dir /tmp/aeroagentsim-docs-media --port 18960 \
  --fps 8 --width 800 --speed 00-overview=1.6 --trim 00-overview=0:22 \
  --speed 02-run-and-operate=4.5 --speed 03-why=1.2 --speed 04-photos=1.3 \
  --speed 05-records-compare=1.4
```
