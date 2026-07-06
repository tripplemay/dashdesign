# 200dpi Print Enhancement Evaluation

Date: 2026-07-05

This evaluation checks whether the first workflow can be improved for text-heavy
print assets. It uses representative crops instead of full posters so each
method can be compared on the same title, small-copy, and QR-code regions.

## Inputs

Sample images:

- `160乘以160.jpg`: square poster, dense copy, QR code.
- `200乘以80.jpg`: wide banner with dense curriculum copy.
- `80乘以180 （打孔）.jpg`: very low-resolution vertical poster.

Generated outputs:

- `quality_eval/run_200dpi/metrics.csv`
- `quality_eval/run_200dpi/README.md`
- `quality_eval/run_200dpi/crops/<crop_id>/<method>.png`
- `quality_eval/run_200dpi/zoom_sheets/*_zoom.jpg`

Command:

```bash
.venv/bin/python scripts/evaluate_print_enhancement.py \
  --output-dir quality_eval/run_200dpi \
  --clean-scratch
```

## Methods Compared

- `pil_current`: current Workflow A equivalent, Lanczos resize + mild
  contrast/color + UnsharpMask.
- `pil_text_strong`: stronger deterministic sharpening.
- `opencv_edsr_x4`: OpenCV `dnn_superres` EDSR x4, then resize to target crop.
- `realesrgan_x4plus`: Real-ESRGAN NCNN general x4 model, then resize.
- `realesrgan_x4plus_anime`: Real-ESRGAN NCNN anime/illustration x4 model,
  then resize.

Installed local tools:

- `tools/realesrgan-ncnn-vulkan`
- `tools/models/`
- `tools/sr_models/EDSR_x4.pb`

## Aggregate Metrics

These are no-reference proxies. Higher gradient/laplacian values usually mean
sharper edges, but can also mean halos, ringing, invented strokes, or QR
damage.

| method | crops | grad_mean_avg | lap_abs_mean_avg | edge_density_avg | clip_ratio_avg | QR decoded |
|---|---:|---:|---:|---:|---:|---:|
| `opencv_edsr_x4` | 9 | 3.015 | 1.648 | 0.0369 | 0.1603 | 1 |
| `pil_current` | 9 | 3.102 | 1.248 | 0.0370 | 0.2018 | 0 |
| `pil_text_strong` | 9 | 3.073 | 1.171 | 0.0366 | 0.2197 | 0 |
| `realesrgan_x4plus` | 9 | 3.509 | 2.847 | 0.0515 | 0.2008 | 0 |
| `realesrgan_x4plus_anime` | 9 | 3.526 | 3.264 | 0.0489 | 0.2596 | 0 |

## Findings

1. The current PIL workflow is stable but not enough for close-view text. It
   creates print-size pixels, but it mostly enlarges existing blur.
2. Stronger UnsharpMask is not a real solution. It can increase contrast, but
   did not improve QR detection and tends to add halos.
3. EDSR is conservative and safer than generative restoration, but the text
   gain is limited. It decoded one QR crop, but did not solve the very
   low-resolution QR sample.
4. Real-ESRGAN `x4plus` gives the best visual improvement for large titles,
   icons, illustration, and many medium-size text regions. It makes strokes and
   backgrounds cleaner than the current workflow.
5. Real-ESRGAN is not safe for QR codes or production-critical copy. In QR
   crops it made the modules look sharper but changed their geometry, so OpenCV
   QR decoding failed on both QR samples.
6. The anime Real-ESRGAN model is more aggressive. It may look sharper on
   illustration, but it has higher clipping/edge energy and more risk of
   over-processing text.

## Judgment

There is real improvement space in the super-resolution engine, but it should
not be treated as a one-pass text recovery tool.

Recommended default for the next print pipeline:

1. Use Real-ESRGAN `x4plus` for image/background/illustration enhancement when
   the source effective DPI is low and the source pixel size is not already
   huge.
2. Keep PIL/Lanczos as a stable fallback for already large images, aspect-ratio
   extension, and final print-size normalization.
3. Never rely on AI upscaling for QR codes, prices, phone numbers, legal copy,
   or brand marks. Decode/regenerate QR codes and re-render text as controlled
   layers.
4. Use GPT image/edit workflows only for background rebuilds or clean artwork
   masters. Do not ask them to render final readable text.

## Next Workflow

The better production workflow is region-aware:

1. Preflight: parse size, target DPI, source effective DPI, aspect mismatch.
2. Segment/mark regions: background, illustration, title, small text, logo, QR.
3. Enhance background and illustration with Real-ESRGAN `x4plus` or a future
   SwinIR/SUPIR candidate.
4. OCR text regions, manually verify the extracted copy, then re-render text at
   print resolution.
5. Decode QR codes from source when possible; otherwise request the target URL
   or payload and regenerate the QR.
6. Composite the rebuilt text/QR/logo layers over the enhanced image layer.
7. Export final flat JPG/PNG and, ideally, a layered production package.

## Candidate Algorithms For Later Rounds

- Real-ESRGAN: practical real-world blind super-resolution; already runnable
  locally via NCNN/Vulkan.
- SwinIR: Transformer-based image restoration; likely worth testing next for a
  less aggressive text/background tradeoff.
- DiffBIR/SUPIR: stronger generative restoration; useful for visual artwork, but
  higher risk for exact text, QR, logos, and local deployment cost.
- Commercial tools such as Topaz Gigapixel or Adobe Super Resolution may be
  worth comparing, but they still cannot replace deterministic text/QR rebuild.

References:

- https://github.com/xinntao/Real-ESRGAN
- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan
- https://docs.opencv.org/4.x/d5/d29/tutorial_dnn_superres_upscale_image_single.html
- https://github.com/JingyunLiang/SwinIR
- https://github.com/XPixelGroup/DiffBIR
- https://github.com/Fanghua-Yu/SUPIR
