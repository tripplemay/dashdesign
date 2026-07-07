# Print Quality Workflow

This project uses complementary workflows for turning low-resolution
agent-generated posters or text prompts into print assets.

## Workflow A: Faithful Repair

Use when the source image is already approved and the goal is to preserve it.

Pipeline:

1. Parse physical dimensions from the filename.
2. Resize to the target print DPI.
3. Preserve aspect ratio; use a blurred background extension when the source
   ratio does not match the target ratio.
4. Apply mild contrast/color enhancement and unsharp mask.
5. Save non-destructive output with DPI metadata.

Command:

```bash
python3 scripts/prepare_print_assets.py \
  --dpi 200 \
  --output-dir workflow_samples/faithful_200dpi \
  --only '80乘以180 （打孔） (2).jpg'
```

Current local backend: Pillow/Lanczos + UnsharpMask.

Evaluated optional backends:

- Real-ESRGAN NCNN/Vulkan x4, runnable locally. It improves large titles,
  illustration, and many medium-size text regions, but it can change QR modules
  and production-critical glyph shapes.
- OpenCV EDSR x4, runnable locally. It is more conservative than Real-ESRGAN,
  but the text improvement is smaller.

Future optional backends:

- SwinIR for Transformer-based super-resolution and artifact cleanup.
- DiffBIR or SUPIR for generative restoration.
- OCR/QR/logo reconstruction for production-critical text and scan codes.

Important rule: do not rely on a generative upscaler to recover QR codes, prices,
phone numbers, or brand marks. Those layers need deterministic reconstruction.

See `docs/print_quality_evaluation_200dpi.md` for the first local comparison
round.

## Workflow B: GPT Image Rebuild

Use when the source image is too low-resolution or when a stronger visual
upgrade is desired.

Pipeline:

1. Create a source preview and metrics profile.
2. Generate a clean-background prompt.
3. Use `gpt-image-2` to generate a high-quality master image.
4. Rebuild text, logo, price, and QR code as deterministic production layers.
5. Feed the final composite through Workflow A for print DPI output.

Command to prepare a package:

```bash
python3 scripts/gpt_image_rebuild.py \
  '120乘以80海报1.jpg' \
  --output-dir workflow_samples/gpt_image_rebuild \
  --print-dpi 200 \
  --api-mode edit \
  --description 'AI drawing education poster: a child creates colorful fantasy art on a glowing tablet in a cosmic digital studio, with magical creatures and neon interface accents.'
```

To execute the GPT Image call after configuring credentials:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
cd workflow_samples/gpt_image_rebuild/120乘以80海报1
./run_gpt_image_generation.sh
```

Use `--api-mode edit` when composition fidelity matters. It sends the source
image to the edits endpoint as a reference. Use `--api-mode generate` only for
looser creative exploration.

The script deliberately asks `gpt-image-2` not to render readable text, QR codes,
prices, or logos. These must be rebuilt as separate controlled layers.

## Workflow C: Baseline Text-to-Image

Use when the source is a text brief instead of an existing poster image.

Pipeline:

1. Load the current project baseline from `docs/baseline/baseline.v1.draft.json`.
2. Inject only the to-C parent/student baseline, visual guidelines, and prompt
   policy into the final image prompt.
3. Create an Image API request package for `gpt-image-2`.
4. Optionally execute the Image API call to generate a master image.
5. Optionally resize the master to the requested centimeter size and DPI.

Command to prepare a package:

```bash
python3 scripts/text_to_image_print.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --output-dir workflow_samples/text_to_image_print \
  --prompt '明亮未来教室中，孩子用平板进行 AI 数字艺术创作，画面包含绘图、视频、漫剧和网页设计的抽象视觉元素，顶部和底部预留文案区域。'
```

To execute the Image API call after configuring credentials:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
python3 scripts/text_to_image_print.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --prompt '明亮未来教室中，孩子用平板进行 AI 数字艺术创作，画面包含绘图、视频、漫剧和网页设计的抽象视觉元素。' \
  --execute \
  --postprocess-print
```

This workflow blocks prompts containing the current C-side baseline's forbidden
B-side business terms unless explicitly overridden for development.

## GPT Image Constraints

The OpenAI image generation guide says `gpt-image-2` supports arbitrary sizes
only when they meet these constraints:

- max edge length <= 3840 px
- both edges are multiples of 16 px
- long edge to short edge ratio <= 3:1
- total pixels between 655,360 and 8,294,400

That is smaller than the required final print pixels for these assets. The GPT
Image output is therefore a master layer, not the final print file.

## Current Prototype Status

- Faithful 200 DPI sample generation is fully runnable locally.
- Baseline text-to-image package generation is runnable locally.
- GPT Image rebuild package generation is runnable locally.
- Actual GPT Image execution is blocked until `OPENAI_API_KEY` is configured.
- OCR/QR/logo reconstruction is not yet implemented in this environment.

Official OpenAI references:

- https://developers.openai.com/api/docs/guides/image-generation.md
- https://developers.openai.com/api/docs/api-reference/images
