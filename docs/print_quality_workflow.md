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

Modes:

- `background`: no-text background output. The prompt keeps readable text, QR
  codes, logos, prices, and phone numbers out of the model image.
- `poster`: background generation plus local typography composition. The image
  model is still asked not to render final text; DashDesign writes exact Chinese
  copy locally into `poster_master.png` and, when print post-processing is
  enabled, the print-ready poster. Poster mode supports local typography
  templates with `--text-style clean_edu` for readable education enrollment
  posters or `--text-style tech_neon` for AI/technology neon posters.

Command to prepare a package:

```bash
python3 scripts/text_to_image_print.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --output-dir workflow_samples/text_to_image_print \
  --mode background \
  --prompt '明亮未来教室中，孩子用平板进行 AI 数字艺术创作，画面包含绘图、视频、漫剧和网页设计的抽象视觉元素，顶部和底部预留文案区域。'
```

Command to prepare a poster-with-copy package:

```bash
python3 scripts/text_to_image_print.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --mode poster \
  --text-style tech_neon \
  --prompt '明亮未来教室，孩子使用平板进行 AI 数字艺术创作，横版海报构图，顶部标题区和中部模块区留白。' \
  --poster-copy $'主标题：AI浪潮已到来，孩子的学习怎能落后\n副标题：AI是未来的核心语言，现在不学，孩子未来就会像文盲一样\n课程类型：\nAI绘图：输入文字，一键生成精美画作\nAI视频：轻松创作专属动画\n结语：限时福利，前50名扫码预约即可获得免费AI课程'
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
  --mode background \
  --execute \
  --postprocess-print
```

This workflow blocks prompts containing the current C-side baseline's forbidden
B-side business terms unless explicitly overridden for development.

## Workflow D: Complete Poster Image2

Use when the priority is strong poster typography and integrated commercial
poster design, and when model-rendered Chinese text can be reviewed before
production.

Pipeline:

1. Load the current to-C project baseline.
2. Parse the user poster copy into exact expected text.
3. Compile the selected purpose, style, layout, text-density, and negative
   prompt templates with the project baseline.
4. Prompt `gpt-image-2` to generate the complete poster, including background,
   headline lettering, module badges, call-to-action, and QR placeholder.
5. Generate multiple candidates.
6. Review candidates against `expected_text.json`.
7. Reject candidates with wrong, missing, duplicated, or extra text.
8. Add the real QR code only after image approval.
9. Feed the approved master through Workflow A for print DPI output.

Command:

```bash
python3 scripts/full_poster_image2.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --image-size 1536x1024 \
  --candidates 4 \
  --purpose-template course_enrollment \
  --style-template tech_neon \
  --layout-template headline_modules_cta \
  --text-density medium \
  --prompt '横版少儿 AI 数字创作招生海报，孩子在未来数字艺术教室中用平板创作，整体要有真实商业海报的标题设计和促销氛围。' \
  --poster-copy $'主标题：驾驭AI浪潮，就是现在！\n副标题：科技革命不等人，AI正在重塑世界，现在是孩子学习AI的黄金窗口期！\n课程类型：\nAI数字绘图：把想象变成精美作品\nAI动态视频创作：让故事动起来\nAI漫剧创编：培养叙事与创作能力\nAI网页&小程序：用作品理解科技\n结语：立即行动，扫码预约免费AI能力测评'
```

Add `--execute` after configuring Image API credentials. The package includes
`prompt_template_profile.json` and `expected_text.json` for manual or future
OCR-based review. If `--prompt` is omitted, the workflow builds the prompt from
the selected templates, current baseline, and poster copy.

See `docs/full_poster_image2_evaluation.md` for the first local evaluation.

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
- Complete-poster Image2 candidate generation is runnable locally.
- Actual GPT Image execution requires `OPENAI_API_KEY` and, when using a
  compatible gateway, `OPENAI_BASE_URL`.
- OCR/QR/logo reconstruction is not yet implemented in this environment.

Official OpenAI references:

- https://developers.openai.com/api/docs/guides/image-generation.md
- https://developers.openai.com/api/docs/api-reference/images
