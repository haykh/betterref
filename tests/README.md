# BetterRef tests

These tests start Blender in background mode with factory settings, register the add-on from this checkout, build small synthetic reference images, invoke the public operators, and validate the resulting image datablocks and empty transforms.

## Windows

```powershell
.\tests\run_tests.ps1 -Blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
```

`-Blender` may be omitted when `BLENDER_BIN` is set or Blender is on `PATH`; otherwise the newest install under `C:\Program Files\Blender Foundation` is used.

## Linux

```sh
./tests/run_tests.sh --blender /path/to/blender
```

Run one group at a time with `--feature geometry` or `--feature crop`. Generated files are placed under `tests/artifacts/` and are cleared before each run unless `--keep-artifacts` is passed.

## What is covered

| Group | Covers |
| --- | --- |
| `geometry` | Quad extents, pixel snapping, the crop transform that keeps content anchored, and the cage matrix round-trip. Pure functions, no scene state. |
| `crop` | Operators end to end: sliced region correctness, anchoring under object transforms, reversibility, property-driven re-bakes, rebuild after save/reload, and registration of the tool, gizmos and panel. |

## What tests cannot cover

Gizmo *drawing* and mouse interaction need a real GPU and window, which background mode has neither of. The suite verifies the gizmo classes register, that the cage's target matrix round-trips the crop rect, and that driving the target handler crops correctly — but the cage's on-screen appearance and drag behaviour have to be checked by hand in the viewport.
