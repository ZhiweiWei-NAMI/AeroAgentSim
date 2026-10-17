# Reproduce the mock person sprite

The mock renderer can use a rectangular crop of an existing real photograph.
Neither the source photograph nor the generated PNG belongs in Git. The helper
uses a local source file and does not download data or run a detector.

## Source and license

- Dataset: [SeaDronesSee Object Detection v2](https://seadronessee.cs.uni-tuebingen.de/dataset).
- File: compressed validation image `16787.jpg`, 3840 × 2160 pixels.
- [Official source image](https://cloud.cs.uni-tuebingen.de/public.php/webdav/Compressed%20Version/images/val/16787.jpg).
- The [authors' README license section](https://github.com/Ben93kie/SeaDronesSee#license)
  states that the dataset uses Creative Commons CC0 1.0 Universal. This was
  verified against the existing cached `AUTHOR-README-LICENSE.md`; the dataset's
  COCO JSON retains a generic `Unknown` license field.
## Prepare from an existing image

Use a Python environment with Pillow installed. From the `AeroRescue` directory:

```sh
python tools/prepare_mock_sprite.py \
  --source /path/to/seadronessee-odv2/images/16787.jpg \
  --output runs/development/mock-visual-assets/person-16787-rgb.png
```

The helper checks image dimensions, converts to RGB, and
applies the already-inspected crop `[1581, 460, 2298, 1475]` in Pillow's
`left, upper, right, lower` coordinates, with right and lower exclusive. The
result is a 717 × 1015 RGB PNG with no generated or retouched person pixels.
The output path is overwritten if it already exists. Pixel content is
reproducible; encoded PNG bytes can vary with the Pillow/zlib version.

Pass the resulting file to the mock runner's `--person-sprite` option. The runner
copies it into the private renderer assets. Do not expose source annotations,
scenario person positions, or crop metadata as detections or agent observations.
The agent must obtain person detections through actual inference on rendered
sensor pixels.

## Scope and limitations

The inspected image shows a person floating in water, wearing a bright
flotation vest. The rectangular crop includes surrounding water and retains
the source perspective. Pasting it onto a terrain scene is visibly artificial.
It is a mock-only visual asset, not a native Gazebo person model, physically
accurate rendering, detector benchmark, or evidence of successful rescue.

Existing development checks ran the actual installed RT-DETR with its unchanged
0.5 confidence threshold. A centered crop rendered at 48–160 pixels high on a
flat-color 640 × 480 canvas produced one person detection per frame; a 32-pixel
crop produced two overlapping detections. These limited source checks do not
establish robustness to motion, occlusion, scene backgrounds, camera geometry,
tracking, geolocation, or flight. The preparation helper does not repeat them.
Renderer scale should follow its declared physical size and camera projection,
not a detector-tuned minimum image size.
