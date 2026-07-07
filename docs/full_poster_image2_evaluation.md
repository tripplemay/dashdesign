# Full Poster Image2 Evaluation

Date: 2026-07-07

This round evaluates a new route where `gpt-image-2` generates the complete
poster, including background, Chinese typography, title effects, course modules,
call-to-action, and QR placeholder area.

## Workflow Under Test

Script:

```bash
python3 scripts/full_poster_image2.py \
  --width-cm 120 \
  --height-cm 80 \
  --dpi 200 \
  --image-size 1536x1024 \
  --candidates 4 \
  --execute \
  --prompt '...' \
  --poster-copy '...'
```

Outputs are written under `workflow_samples/full_poster_image2_eval/`.
Each package includes:

- `prompt.md`: final complete-poster prompt.
- `expected_text.json`: exact Chinese text to review in the image.
- `candidate_XX/full_poster_master.png`: generated complete poster.
- `status.json`: API status, returned dimensions, and manual OCR requirement.

OCR is not automated in the current local environment because neither
Tesseract nor EasyOCR is installed. Manual text review was used for this round.

## Samples

### Landscape AI Course Poster

Package:
`workflow_samples/full_poster_image2_eval/20260706_235535_120x80_full_poster_image2`

Result:

- 4 candidates requested.
- 4 candidates generated successfully.
- Returned size: `1536x1024` for all candidates.
- Manual review: at least 3 of 4 candidates were visually strong enough to
  enter selection or minor correction.
- Best checked candidates kept the main headline, subtitle, module titles,
  module details, CTA, and blank QR placeholder readable and mostly exact.

Selected print output:
`candidate_01/print_ready/120乘以80_整图海报候选01_200dpi.jpg`

Print size:
`9449x6299` px at 200 DPI.

### Portrait AI Drawing Workshop Poster

Package:
`workflow_samples/full_poster_image2_eval/20260707_001707_80x120_full_poster_image2`

Result:

- 2 candidates requested.
- 2 candidates generated successfully.
- Returned size: `1024x1536` for both candidates.
- Candidate 2 was the stronger result. It showed a clear poster headline,
  integrated illustrated background, readable module area, CTA, and blank QR
  placeholder.
- Candidate 1 was visually usable but had denser small text and higher
  readability risk.

Selected print output:
`candidate_02/print_ready/80乘以120_整图海报候选02_200dpi.jpg`

Print size:
`6299x9449` px at 200 DPI.

### Square Comprehensive Course Poster

Package:
`workflow_samples/full_poster_image2_eval/20260707_002935_160x160_full_poster_image2`

Result:

- 2 candidates requested.
- 1 candidate generated successfully.
- 1 candidate failed with gateway `504`.
- Returned size for candidate 1 was `1254x1254`, not the requested
  `1536x1536`.
- Visual quality was strong, but the model rewrote several required copy items.
  This candidate is not production-ready without regeneration or local copy
  correction.

## Findings

The complete-poster route is visually much stronger than the local typography
composition route. It produces real poster title lettering, integrated glow,
module badges, image-text fusion, and stronger commercial composition.

The main risk is not visual quality. The main risks are:

- Chinese text drift: the model may rewrite, shorten, or embellish required
  copy, especially in dense square layouts.
- Small text readability: long module copy becomes risky when many modules are
  present.
- API stability: square high-quality generation produced one gateway timeout in
  this round.
- Returned dimensions may differ from the requested size on some calls.
- QR codes must remain blank placeholders. Real scannable QR should be added
  after generation.

## Recommendation

Promote complete-poster image2 generation to the next primary exploration
workflow, with a guardrail loop:

1. Generate 3-4 candidates per poster.
2. Review against `expected_text.json`.
3. Reject candidates with wrong, missing, duplicated, or extra text.
4. For strong candidates with minor text defects, use image edit or local
   correction on the affected region.
5. Add the real QR code after candidate approval.
6. Run the approved master through the existing 200 DPI print post-processing.

This route should not replace the deterministic local-composition workflow yet.
It should become a separate "complete poster" mode optimized for visual quality
and poster typography.
