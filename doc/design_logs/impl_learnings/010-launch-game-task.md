# Implementation Learnings — 010 Launch Game Task

## 1. Template auto-loader picks up new PNGs for free

**Assumption:** New templates like `CADPA16` and `CLOSENOTICE` would need to be registered explicitly on the `Templates` / `IMG` class in `fgoDetect.py`, so a code change plus the PNG file.

**Reality:** Line 11 of `fgoDetect.py` builds `IMG` with a dict comprehension over `os.listdir('fgoImage')`, so *any* new PNG becomes an attribute automatically (uppercased, alpha split into `(BGR, mask)`). Just dropping `cadpa16.png` and `closenotice.png` into `fgoImage/` was enough — `IMG.CADPA16` and `IMG.CLOSENOTICE` existed without a single line of Python edited on the loader side.

**Fix:** None needed. Verified with `python -c "import fgoDetect; print(fgoDetect.IMG.CADPA16[0].shape)"` — returned `(67, 67, 3)`.

**Takeaway:** Before adding scaffolding for a new asset, check whether the existing loader is already generic. In this codebase, the whole `fgoImage/` folder is one-line auto-registered.

## 2. Detectors go on `XDetectBase`, not `DetectCN`, when templates are region-agnostic

**Assumption:** Per the design log, add `isCadpaLogo` / `isCloseNotice` to `DetectCN` since they're only tested against the CN client.

**Reality:** The template loader puts CN-specific images in `fgoImage/cn/` (attached to `IMG_CN`) but base-level images in `fgoImage/` (attached to `IMG`). The CADPA badge and close-X are identical assets across all regions — I placed them at the base `fgoImage/` level, so they should be accessible from every region. Methods that use base templates belong on `XDetectBase` where `isMainInterface` already lives.

**Fix:** Added the three methods next to `isMainInterface` on `XDetectBase`. All four `DetectCN/JP/NA/TW` subclasses inherit them for free.

**Takeaway:** Match the location of the method to the location of its template. Base-folder template → base-class detector.

## 3. `_find` returns center coordinates already

**Assumption:** Locating the close-notice X for a tap would need `_loc` plus arithmetic to add half-width/half-height to the template's top-left match position.

**Reality:** `XDetectBase._find` (line 58) already returns the *center* pixel of the match: `(rect[0]+loc[2][0]+(img[0].shape[1]>>1), rect[1]+loc[2][1]+(img[0].shape[0]>>1))`. It also returns `None` when the match is below threshold, which is exactly the guard we want.

**Fix:** `locateCloseNotice` collapsed to a one-liner: `self._find(self.tmpl.CLOSENOTICE, (1180,0,1280,80))`. No manual arithmetic, no manual threshold check.

**Takeaway:** Read the base-class helpers before writing your own. Every helper on `XDetectBase` is a one-liner but does something specific — `_loc` for raw match, `_compare` for boolean, `_find` for center-coordinate-with-threshold, `_select` for picking-among-N.

## 4. LDPlayerDevice keeps `._console` + `._index` + `.package` for us

**Assumption:** Launching the FGO app would need to reach into `emu.ldplayer` and re-detect the console path / running instance / package name.

**Reality:** `LDPlayerDevice.__init__` already stores `self._console` (an `LDConsole` wrapper with `launch_app(index, package)`), `self._index`, and `self.package` (auto-detected via `pm path` against `PACKAGE_TO_REGION`). The kernel only needs to reach through `fgoDevice.device.I` (the input side of the `Device` wrapper) to get all three.

**Fix:** `launchGame` uses `getattr(fgoDevice.device,'I',None)` to grab the underlying `LDPlayerDevice` and calls `dev._console.launch_app(dev._index, dev.package)`. Falls back to a warning log if the device isn't LDPlayer (e.g., generic ADB device), letting the poll loop still work if FGO was already open.

**Takeaway:** When a wrapper class exposes internal attributes with an underscore, they're not actually private — check them before duplicating logic. And route through `.I` / `.O` when working with the `Device` wrapper, which multiplexes input and output devices.

## 5. `schedule.sleep(1.0)` after the close-tap breaks the close-tap itself

**Assumption:** Adding a `schedule.sleep(1.0)` right after `fgoDevice.device.touch(pos)` on the close-notice branch would let a second stacked notification finish rendering before the next loop iteration, without affecting the tap that just happened.

**Reality:** With the extra sleep in place, the close-notice X stopped registering — the modal stayed open forever. Removing the sleep restored the behavior. Root cause unconfirmed; suspects include:
- Some interaction between `schedule.sleep` and the LDPlayer PostMessage input queue that swallows or delays the click event.
- The existing trailing `schedule.sleep(2.0)` at the end of the loop already provides enough time for stacked modals to appear on the next iteration, making the intermediate sleep redundant.

**Fix:** Removed the post-close sleep. Stacked-notice handling relies purely on the loop's trailing 2 s pause; empirically this is enough for the second modal (系统公告 → 游玩指引) to render and be detected on the next pass.

**Takeaway:** Do not add sleeps immediately after input events in this codebase without testing — the LDPlayer input path is sensitive to timing in non-obvious ways. Prefer adjusting the loop's outer cadence over inserting per-event pauses.

## 6. Default `_compare` threshold (0.05) is too tight for resized screenshots

**Assumption:** Templates cropped from real screenshots would match at the default `_compare` threshold of `0.05` (SQDIFF-normalized), like every other detector in `fgoDetect.py` does.

**Reality:** `isCloseNotice` returned `False` on live emulator screenshots even though the X was clearly visible in the expected position. Debugging with `cv2.matchTemplate` showed the min SQDIFF-normalized score was **0.0915** — off by ~2×. The template pixels came from a manual raw crop, but `LDPlayerDevice.screenshot()` resizes native emulator output through `cv2.INTER_CUBIC` to 1280×720. That resize smooths pixels enough to push identical-looking assets out of the tight 0.05 threshold.

**Fix:** Two-step:
1. First, bumped the threshold to `0.15` for `isCloseNotice`, `locateCloseNotice`, and `isCadpaLogo` to unblock the pipeline. Non-matches score >0.3 in practice, so 0.15 is safely below the noise floor.
2. Then re-cropped `closenotice.png` directly from a live-emulator screenshot (post-`INTER_CUBIC`) at a tighter 43×38 bbox with a rounded-rectangle alpha mask that excludes the button's outer corners (where the notification background bleeds through and varies between popup styles). With that template, live-emulator popups score `0.0000` exact, so the threshold was tightened back to `0.10` for the close-notice detectors. `isCadpaLogo` stays at `0.15` since its template is still from a raw asset.

**Takeaway:** When adding a template, prefer cropping it from a real `LDPlayerDevice.screenshot()` output rather than from a raw art asset — the INTER_CUBIC resize path is not lossless, and templates that don't go through it end up with a ~2× worse match score. If the button sits on varying backgrounds across popup styles, use an alpha mask that covers only the button interior (rounded-rect / ellipse), not the raw rectangular crop.
