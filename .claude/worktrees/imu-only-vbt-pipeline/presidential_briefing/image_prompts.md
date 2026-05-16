# IMAGE GENERATION PROMPTS — Project Velocitas Briefing

Generate each image in the suggested resolution. Save into `images/` with the
exact filename indicated so the LaTeX template picks them up without edits.

All prompts are written for a high-end T2I model (SDXL, Flux, Imagen, Midjourney v6+).
Aim for **editorial / pharmaceutical / luxury-tech advertising aesthetic** — restrained,
premium, never gamified or "fitness app" looking.

Universal style anchors to include in every prompt:
- Professional editorial photography
- Cinematic lighting, soft rim light, deep shadows
- Color palette: deep navy (#0A1428), warm gold (#C9A227), clean white
- Photorealistic, ultra-detailed, 8k, depth of field
- Magazine cover / Nature journal aesthetic
- No text or watermarks anywhere in the image

---

## 1. `hero.jpg` — Right-column hero image (PRIMARY)
**Resolution:** 1200 × 900 (4:3) — portrait of motion + technology

> A hyper-realistic editorial photograph of a male athlete mid-rep in a deep
> back squat, captured from a slight low angle. The athlete is shot in
> high-contrast cinematic chiaroscuro lighting — deep shadows, a single warm
> overhead key light spilling along the bar and the athlete's shoulder. The
> setting is a modern, minimalist research laboratory that doubles as a
> training space: matte black floor, polished concrete walls, a soft amber
> ambient glow. A small, sleek matte-black sensor module the size of a
> bottle cap is clipped to the barbell sleeve, with a single tiny gold LED
> glowing on its face. The atmosphere is serious, scientific, almost
> sacred — like a Nature journal cover crossed with a luxury sports brand
> campaign. Color grade: deep navy shadows, warm gold highlights, desaturated
> midtones. Shot on Hasselblad H6D, 80mm lens, f/2.8. No text, no logos.

---

## 2. `faculty_crest.png` — Top-left placeholder logo (transparent PNG)
**Resolution:** 600 × 600 (1:1) — clean vector-style emblem

> A minimalist circular academic crest, flat vector aesthetic, on a fully
> transparent background. A stylised emblem combining an inductor coil
> symbol intertwined with a stylised neuron / DNA helix shape, framed inside
> a thin double-line gold circle. Below the symbol, a subtle laurel half-wreath.
> Colors: deep navy (#0A1428) base with warm gold (#C9A227) accents,
> nothing else. The crest must look like an authentic European faculty
> emblem — restrained, classical, scholarly. No text, no banner, no letters.
> Symmetrical, perfectly centered. Transparent background, ready for print.

---

## 3. `university_crest.png` — Top-right placeholder logo (transparent PNG)
**Resolution:** 600 × 600 (1:1) — clean vector-style emblem

> A minimalist circular university seal, flat vector aesthetic, on a fully
> transparent background. A classical shield shape framed inside a thin
> double-line gold ring. The shield contains a stylised open book at the
> base and an abstract rising sun or atom shape above it. The aesthetic
> evokes an authentic 20th-century university seal — dignified, scholarly,
> not corporate. Colors: deep navy (#0A1428) base, warm gold (#C9A227)
> highlights, ivory background inside the shield. Symmetrical, perfectly
> centered, fully transparent surrounding background. No text, no letters,
> no banners.

---

## 4. Optional swap-ins (if you want richer visuals)

### 4a. `hero_alt_chip.jpg` — Macro of the prototype chip
**Resolution:** 1200 × 900 (4:3)

> Extreme macro photograph of a single black-packaged microchip, sized like
> a grain of rice, resting on the precisely lit surface of a stainless-steel
> medical instrument tray. A pair of gloved surgeon's fingertips just enters
> the frame holding fine tweezers. The chip catches a single specular warm
> gold highlight on its bevelled edge. Background is out of focus, hinting
> at clinical white surfaces and warm laboratory lighting. Editorial,
> luxury-tech, Apple-product-launch aesthetic crossed with a medical journal
> cover. Color palette: deep navy shadows, polished steel, warm gold accent.
> Shot on Phase One, 120mm macro, f/4. No text, no logos.

### 4b. `hero_alt_athlete.jpg` — Rehabilitation framing
**Resolution:** 1200 × 900 (4:3)

> Editorial photograph of a young female athlete on a physiotherapy bench
> in a modern clinical rehabilitation suite, performing a guided leg-press
> motion. A discreet matte-black sensor with a single small gold indicator
> is fastened to her ankle. A physician in a tailored white coat observes
> intently from the side, holding a tablet. Lighting is soft, clinical,
> dignified — like a hospital documentary by Annie Leibovitz. Background:
> floor-to-ceiling windows with diffused daylight, polished pale-grey walls.
> Color grade: cool desaturated tones with warm gold highlights on the
> sensor. Hopeful, serious, premium. No text, no logos.

### 4c. `hero_alt_lab.jpg` — Team / faculty framing
**Resolution:** 1200 × 900 (4:3)

> Wide editorial photograph of an advanced electronics research laboratory
> at twilight. Three researchers in dark turtlenecks and lab coats stand
> grouped around a large illuminated workbench covered in oscilloscopes,
> a printed-circuit prototype, and a microscope. Above them, blue and gold
> ambient LED strips wash the ceiling. Soft, cinematic, painterly lighting.
> Mood: dignified, ambitious, scholarly. No faces clearly visible — shot
> from behind or three-quarter, focus on the equipment and atmosphere.
> Color grade: deep navy shadows, warm gold highlights, magazine-quality
> color science. No text, no logos.

---

## 5. Optional decorative ornaments

### 5a. `divider_ornament.png` — Page-break flourish (transparent PNG)
**Resolution:** 1600 × 80 (20:1) — horizontal strip

> A thin elegant horizontal ornament, gold foil aesthetic, art-deco
> inspired. A central small diamond motif flanked by symmetric tapering
> lines that fade into transparency at the edges. Color: warm gold
> (#C9A227) only. Transparent background. Print-ready, sharp at any
> scale. No text.

### 5b. `bg_pattern.png` — Subtle background watermark (transparent PNG)
**Resolution:** 2000 × 2828 (A4 ratio)

> A very subtle, almost invisible decorative pattern of overlapping
> concentric circles and engineering blueprint lines, in 4% opacity gold
> on transparent background. Inspired by classical academic engraved
> letterhead. Print-ready. No text.

---

## NOTES FOR THE OPERATOR

- Generate each image in **at least the resolution suggested** — you can
  downsize but never upsize.
- If your model adds spurious text on the chip / sensor / lab equipment,
  re-roll. The brief tolerates **zero on-image typography**.
- If the model insists on showing a brand or sport logo, add the negative
  prompt: `--no text, --no logo, --no brand, --no watermark, --no letters`
- The `hero.jpg` is the only image that's structurally required. Crests
  can be left as the model's first usable output; we will refine in v2.
