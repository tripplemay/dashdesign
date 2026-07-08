# Prompt Template Library

DashDesign uses structured prompt templates to turn short user input into
professional image-generation briefs.

Current library:

- `full_poster_templates.v1.json`

The library is used by the complete-poster Image2 workflow:

```bash
python3 scripts/full_poster_image2.py \
  --template-library docs/prompt_templates/full_poster_templates.v1.json \
  --purpose-template course_enrollment \
  --style-template tech_neon \
  --layout-template headline_modules_cta \
  --text-density medium \
  --negative-template full_poster \
  --width-cm 120 \
  --height-cm 80 \
  --poster-copy '...'
```

Template dimensions:

- Purpose: enrollment, trial class, AI assessment, or course system.
- Style: tech neon, bright education, fantasy AI art, premium minimal, or comic
  pop.
- Layout: title/modules/CTA, central subject, portrait standee, or square social.
- Text density: low, medium, or high.
- Negative constraints: workflow-specific rules such as no extra text and no
  scannable QR codes.

For complete-poster Image2 mode, user visual prompts are optional. If the user
does not write a prompt, the compiler uses the selected templates, project
baseline, and poster copy to build the final prompt.

Every generated package records:

- `prompt.md`: final compiled prompt.
- `prompt_template_profile.json`: exact templates and blocks used.
- `expected_text.json`: required text for manual or OCR review.
- `generation_record.json`: template version and selected template IDs.
