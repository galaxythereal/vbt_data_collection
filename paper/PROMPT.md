# The prompt for generating the paper

Copy the fenced block below. It is written against
[`paper/PAPER_SOURCE.md`](PAPER_SOURCE.md), which is the authoritative source: 48 measured
claims each naming a re-runnable script, a 54-entry decision log, and an asset inventory
with every path verified.

**Why the prompt is shaped the way it is.** A model asked to write an IEEE paper will smooth
over gaps with plausible-sounding numbers and plausible-sounding citations, because that is
what makes prose flow. On this project that would be fatal: the whole value of the work is
that every figure is traceable and every negative result is reported. So most of the prompt
is not about style — it is about making fabrication structurally impossible (write a
placeholder instead), keeping proposals from being promoted to results, and keeping the
caveats that a persuasive draft would quietly drop. §2 below explains each rule.

---

## The prompt

```
You are writing a paper for a top-tier IEEE conference (instrumentation /
biomedical sensing / wearables). Your job is to turn an existing, complete
engineering record into a submission-quality paper. You are not doing new
analysis and you are not deciding what is true — that is already settled and
written down.

SOURCE OF TRUTH

  paper/PAPER_SOURCE.md is the only source for facts, numbers, and claims.
  Read it in full before writing anything. Also read, as needed:
    docs/INERTIAL_ENGINE.md          the inertial design record
    docs/RESEARCH_FINDINGS_CHECKED.md  external reports checked against the corpus
    paper/*.tex                      existing drafted sections, to reuse and revise
    paper/references.bib             the only citation keys you may use

  Five rules, and breaking any of them makes the output useless:

  1. NEVER INVENT A NUMBER. Every numeric claim must appear in the source. If
     the paper needs a number that is not there, write
     [NUMBER NEEDED: what, and which script would produce it]
     inline and continue. Do not estimate, do not round differently to make a
     sentence read better, do not derive a new figure from two existing ones.

  2. NEVER INVENT A CITATION. Cite only keys present in paper/references.bib,
     or comparators listed in PAPER_SOURCE.md §8.4 with their criterion. If a
     claim needs support that does not exist, write
     [CITATION NEEDED: the claim]
     Do not reconstruct authors, journals, years, volumes or page numbers from
     memory — a wrong journal is a retraction risk.
     PROVENANCE IS SPLIT, and PAPER_SOURCE.md §1.1 says so: the orientation-
     estimation citations were read directly, but the velocity-based-training
     device citations reached this project through commissioned literature
     reports, and one of those reports placed Fritschi et al. 2021 in the wrong
     journal. Treat every VBT bibliographic detail as unverified. The CLAIMS in
     §1.1 and §8.4 are safe to make; the volume, issue and page numbers are
     not. Mark each as [CITATION TO VERIFY: ...] unless it is already in
     references.bib.

  3. RESPECT THE TAGS. The source marks every claim:
       [M] measured on this corpus  -> may be stated as a result
       [L] from the literature      -> must be attributed
       [I] inferred or proposed     -> must be written as future work or as a
                                       stated assumption, NEVER as a result
     Promoting an [I] to a result is the most damaging error you can make here.

  4. KEEP THE CAVEATS. These are load-bearing and a persuasive draft will want
     to drop them. All must survive into the paper:
       - the per-repetition camera-free figures are on 94.9% of the released
         repetitions, and the reference-boundary baseline is on all 1400, so
         the latter is scored on a harder set;
       - the 0.1 Hz high-pass is zero-phase and non-causal, so a real-time
         device cannot have it and its causal cost is UNMEASURED;
       - the lever arm is a camera-derived constant that cannot be recovered
         from the IMU;
       - heading and absolute height are not observable from a 6-axis IMU;
       - the -0.14% accelerometer scale error may be a gravity-model error and
         has not been checked;
       - the synchronisation figure (1.17 ms) is unreconciled against an
         earlier 0.92 ms at lower pair retention.

  5. KEEP THE NEGATIVE RESULTS. PAPER_SOURCE.md §9 lists nine, each with the
     measurement that closed it. They are a contribution, not an appendix to be
     trimmed. Six were recommended by a majority of five external research
     reports, which is itself worth one sentence.

STRUCTURE AND BUDGET

  THIS IS A DATASET, INSTRUMENTATION AND VALIDATION PAPER. The setup, the time
  transfer, the ground-truth annotation, the validation, and the inertial
  pipeline evaluated against them carry ROUGHLY EQUAL WEIGHT. The inertial work
  is one pillar of five, not the centrepiece. Use the allocation in
  PAPER_SOURCE.md §11.1 and do not let §VIII grow at the expense of §V and §VI
  — the annotation and its validation are what make the corpus worth releasing,
  and they are the two things no comparable dataset provides.

  Target 10 pages, IEEE two-column. If the venue caps at 8, cut in the order
  §11.1 gives, which does not touch §V or §VI.

  The introduction must open with velocity-based training itself, from
  PAPER_SOURCE.md §1.1: what the practice is, why per-repetition bar velocity
  is the measurand, and — this is the argument that makes the paper's error
  analysis matter — why a fixed offset and random scatter are NOT
  interchangeable, because an offset shifts an athlete's whole load-velocity
  profile and every intensity estimate drawn from it, while scatter averages
  out. Then the gap: no released corpus pairs per-frame reference kinematics
  carrying a measured uncertainty with a repetition annotation whose rules are
  written down and whose boundaries are individually reviewed.

  Lead the contributions with C1-C7 from PAPER_SOURCE.md §1.2, in that order —
  they are already ranked by defensibility, and C1 (the corpus) leads because
  this is a corpus paper. State plainly which two are new as far as we know
  (C2, C3), and keep the "to our knowledge" hedge; do not upgrade it to
  "the first".

STYLE

  - Plain, direct, technical. No "in recent years", no "with the rapid
    development of", no "paradigm", no sentence whose only job is transition.
  - Every method statement carries its reason. PAPER_SOURCE.md §13 has 54
    decisions with reasons; a method described without its reason is a worse
    paper than one sentence longer. Example of the register wanted: not "the
    vertical axis was taken from the camera frame" but "the vertical is the
    camera's own optical axis rather than a fitted direction: the two correlate
    at +0.9998 across all 84 sessions, so fitting would add a free parameter
    per session for no measurable gain."
  - Report reversals where they carry information. 34 entries in the decision
    log are marked [R] — things this project got wrong and what the measurement
    said. A paper that says "we implemented the iterated, robust, acausal and
    hand-rolled filters and here is the measurement showing why they are
    equivalent" is stronger than one reporting a smaller number with no account
    of what was tried. Do not turn the paper into a confessional either: include
    a reversal when it prevents a reader from repeating the mistake or explains
    why a design looks the way it does.
  - Statistics: report RMSE, bias with 95% limits of agreement, SEE, r, and
    CV. NEVER convert between statistics — an RMSE is not a limit of agreement
    and neither is comparable with a correlation. Report per relative load and
    per exercise where the source does, because pooling hides the
    heteroscedasticity that is one of the findings.

FIGURES AND TABLES

  PAPER_SOURCE.md §11.2 lists ten figures with the script that makes each, and
  §11.3 lists thirteen tables. Use those. Four figures need plotting code that
  does not exist yet (Bland-Altman, the ablation, the common/differential
  slopes, the lever-arm bound) — mark them
  [FIGURE TO GENERATE: which script, what it should show]
  rather than describing an image that does not exist.
  Figure style is already established and should be reused: white background,
  Okabe-Ito palette (#000000 camera, #0072B2 VQF, #D55E00 ESKF, #009E73
  concentric, #E69F00 eccentric), 9 pt sans, top and right spines hidden,
  y-only grid, 300 dpi. See scripts/imu/audit_pipeline.py.

OUTPUT

  LaTeX, as one file per section following the existing naming
  convention in paper/ (a two-digit order prefix then the section name), using the packages already in paper/main.tex (siunitx, booktabs,
  natbib). Every section file starts with a comment naming the scripts that
  produced its numbers, as paper/10_inertial_baseline.tex already does.

BEFORE YOU FINISH

  Run these checks on your own output and report the result:
  1. List every number in the paper that you could NOT trace to a specific line
     of PAPER_SOURCE.md. This list should be empty; if it is not, say so.
  2. List every citation key you used and confirm each exists in
     references.bib.
  3. List every [I]-tagged item from the source and state where each appears in
     the paper — it must be future work or a stated assumption, never a result.
  4. Confirm each of the six caveats in rule 4 appears in the paper, and where.
  5. Report the page estimate against the 8-page budget.
```

---

## 2. Why each rule is there

| rule | the failure it prevents |
|---|---|
| never invent a number | the highest-probability failure. A model writing methods prose will produce "approximately 50 mm/s" where the source says 47.9, or invent a sample size to fill a sentence. The placeholder mechanism gives it a legal way out. |
| never invent a citation | second highest, and less recoverable — a fabricated journal or volume survives review-by-reading and fails review-by-checking. One of the five external research reports on this project placed Fritschi et al. 2021 in the wrong journal, which is exactly this failure in the wild. |
| respect the tags | the source deliberately separates measured from proposed. Nine of the ten open items in `INERTIAL_ENGINE.md` §10 are unmeasured, and several are attractive enough to write up as though done — the causal cost of the high-pass, the per-exercise gain correction, the gravity-model check. |
| keep the caveats | a persuasive draft drops the 94.9% matched fraction, and then the headline "camera-free beats camera-boundary" becomes false, because the baseline is on all 1400. Same for the non-causal filter: without that sentence the paper implies a real-time result it does not have. |
| keep the negative results | they are contribution C7, they are what makes C5 (the NIS argument) land, and they are the answer to "why did you not try filter X". |

## 3. Staging

One shot for 8 pages will thin out in the later sections. Better order, one prompt per stage,
each carrying the block above plus a line saying which section to write:

1. **§VII, §VIII, §IX** first — the contributions and the results. Writing these first
   disciplines everything else, because the claims are then fixed.
2. **§IV, §V, §VI** — methods, written to support exactly the claims made in step 1 and
   nothing more.
3. **§III** — instrument; largely revision of the existing `02_`–`04b_` drafts.
4. **§X, §XI** — negative results and limitations.
5. **§I, §II** last — the introduction and related work, written to promise exactly what the
   paper delivers. Writing the introduction first is how papers end up over-claiming.
6. **A consistency pass** over the whole thing with only this instruction: *find every
   number that appears twice and confirm the two agree; find every claim in the introduction
   that no later section supports.*

## 4. If the target is a Claude Design canvas rather than LaTeX

Replace the OUTPUT block with:

```
OUTPUT

  A design canvas: one artboard per paper page in IEEE two-column proportions,
  plus a first artboard carrying the title, authors, abstract and the
  contribution list. Typeset for reading on screen at 100% as well as in print.
  Figures are the released PNGs where they exist — datasets/session_*/
  audit_pipeline_vqf.png, audit_pipeline_eskf.png, audit_imu.png,
  audit_post_session.png, datasets/bar_path_imu_vs_camera.png — placed at their
  native aspect ratio, never stretched. Tables are typeset, not screenshots.
  Keep the Okabe-Ito palette so the figures and the page furniture agree.
```

Everything above the OUTPUT block stays as-is: the five rules matter more for a designed
document than for LaTeX, because a visually finished page invites less scrutiny of whether
its numbers are real.
