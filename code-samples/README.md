# Code samples

A few representative files from the Eir Count apps and tooling, copied as they are in the project. They show how the code is written. They are not a buildable app: they refer to other files that are not included, such as the camera screen, settings and the model itself.

## Android (Kotlin)

| File | What it shows |
|---|---|
| [PillDecoder.kt](android/PillDecoder.kt) | Turns the model's two output maps into pill dots: peaks of the centre heatmap, inside the largest tray region |
| [BlurCheck.kt](android/BlurCheck.kt) | The blur measure: the width of pill edges in their blurriest direction, as the median over up to 64 pills |
| [OverlapCheck.kt](android/OverlapCheck.kt) | Finds spots where pills may be stacked, from the shape of the heatmap around each dot |
| [CapturePolicy.kt](android/CapturePolicy.kt) | Decides retake, save or "Please check" for each photo, and the retake notice |
| [DeviceTier.kt](android/DeviceTier.kt) | Times the model once per phone and chooses live dots or light mode. Drops a slow NPU for the GPU |
| [Fill.kt](android/Fill.kt) | Sweep to target as pure logic: a pour photo and a check photo linked into one fill |

## iPhone (Swift)

| File | What it shows |
|---|---|
| [PillDecoder.swift](ios/PillDecoder.swift) | The same decoder as Android, tested against the same reference |
| [DeviceTier.swift](ios/DeviceTier.swift) | The iPhone's speed tiers |
| [Fill.swift](ios/Fill.swift) | Sweep to target on iPhone |

## Python

| File | What it shows |
|---|---|
| [capture_checks.py](python/capture_checks.py) | The reference version of the blur and overlap checks. The Kotlin port is tested against it on recorded test cases |
| [train_v5.py](python/train_v5.py) | Training for the EfficientNetV2-S model line (backbone, feature pyramid, two-map head, augmentation, checkpoints) |
| [render_v3.py](python/render_v3.py) | The Blender scene generator for photorealistic training renders: trays, capsules, tablets, softgels, physics piles, glare, shadows and exact labels |
| [testlab.py](python/testlab.py) | Runs speed and parity tests on real phones in Firebase Test Lab, with a hard monthly spending cap |

© 2026 Asgard Pharmaceuticals. All rights reserved. Shared for viewing only.
