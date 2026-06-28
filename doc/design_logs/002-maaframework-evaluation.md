# Design: MaaFramework Integration Evaluation

**Date**: 2026-06-27  
**Status**: Rejected  
**Author**: @liz2020

## Summary

Evaluated whether [MaaFramework](https://github.com/MaaXYZ/MaaFramework) (LGPL-3.0) could benefit FGO-py. Conclusion: **not worth adopting** as a dependency, but can be referenced as an algorithm source when needed.

## What is MaaFramework?

A cross-platform automation framework based on image recognition, originally derived from MaaAssistantArknights. Provides:

- JSON pipeline-based task definition (low-code)
- Built-in recognition: TemplateMatch, FeatureMatch (SIFT/ORB), ColorMatch, OCR, NeuralNetworkClassify, NeuralNetworkDetect
- Cross-platform controllers (Windows, Linux, macOS, Android via ADB)
- Python binding: `pip install maafw`
- 50+ community game bots built on it

## Comparison: Detection Methods

| MaaFW Algorithm | FGO-py Equivalent | Adds value? |
|-----------------|-------------------|:-----------:|
| TemplateMatch | `_compare/_find/_select` (cv2.matchTemplate) | No — identical |
| FeatureMatch (SIFT/ORB) | *(none)* | No — game is fixed 720p, no scale/rotation |
| ColorMatch (HSV/RGB) | `numpy.mean()` pixel checks | Marginal |
| OCR (PaddleOCR ONNX) | `pponnxcr` (same engine) | No — identical |
| NeuralNetworkClassify | `_select()` with templates | No — templates already 100% at 720p |
| NeuralNetworkDetect (YOLO) | *(none)* | No — enemy positions are fixed slots |

## Comparison: UI Navigation

FGO-py currently has **no general-purpose menu navigation** — each function assumes you're already on the main interface. MaaFW's pipeline model (`next` + `on_error` + timeout retry) naturally handles "from anywhere → reach destination" flows.

However, the existing MaaFgo project (xlxyvergil/MaaFgo) demonstrates that even with MaaFW:
- Map navigation still requires a Custom Action with the **same OpenCV algorithm** FGO-py uses
- ~60-70% of navigation logic still ends up as Custom Python code
- The "hard part" (camera detection, vector math, iterative swiping) is identical

### Cost of adopting MaaFW for navigation

| Gain | Cost |
|------|------|
| Declarative retry/timeout for screen transitions | 30MB native dependency |
| Generic UI (MWU/MXU) for free | Two-process architecture (Agent + Core) |
| Non-programmers can add quests via JSON | JSON verbosity (~800 lines replacing ~110 lines Python) |
| | Harder debugging (opaque pipeline timeouts vs stack traces) |
| | FGO-py already has GUI + CLI + Web UI |

## Reference Project: xlxyvergil/MaaFgo

MaaFgo uses MaaFramework for FGO automation but takes a fundamentally different approach:

- **Battle**: Delegated entirely to BBchannel (pre-scripted turn-by-turn commands)
- **Navigation**: MaaFW pipelines for menu traversal + Custom Action for map navigation
- **Philosophy**: "I know the 3T loop, just execute it" — opposite of FGO-py's adaptive AI

MaaFgo's map navigation Custom Action literally uses the same algorithm as FGO-py's `fgoReishift.py` (same crop region, scale factor, polygon, offset). Their README credits FGO-py for this.

## License Compatibility

| | MaaFramework | FGO-py |
|---|---|---|
| License | LGPL-3.0 | AGPL-3.0 |
| Copyleft scope | Library only | Entire project |

- ✅ Can read MaaFW source for algorithm reference
- ✅ Can reimplement same OpenCV/ONNX algorithms (standard public APIs, not copyrightable)
- ✅ Can `pip install maafw` and link as library (LGPL allows this in AGPL projects)
- ✅ AGPL ⊇ LGPL — no compatibility conflict

## Decision

**Do not adopt MaaFramework as a dependency.** Reasons:

1. FGO-py's detection already achieves 100% accuracy at fixed 720p resolution
2. Both use the same underlying engines (OpenCV, PaddleOCR ONNX)
3. The pipeline model adds indirection without benefit for AI-driven battle logic
4. Navigation code is compact (~110 lines) and tightly integrated
5. MaaFW's advanced detectors (SIFT, YOLO) solve problems FGO doesn't have

**Do reference MaaFW's implementations** when new detection methods are needed in the future. The algorithms are standard OpenCV/ONNX patterns that can be implemented in ~20-50 lines each.

## Future Considerations

If FGO-py ever needs:
- **Robust "return to home from any screen"** — consider implementing a simple state machine (~200 lines) rather than adopting MaaFW's full pipeline system
- **Feature matching** (e.g., if game UI becomes dynamic) — implement SIFT wrapper directly (~30 lines)
- **Object detection** (e.g., variable enemy counts) — train a small ONNX model, load via onnxruntime directly
