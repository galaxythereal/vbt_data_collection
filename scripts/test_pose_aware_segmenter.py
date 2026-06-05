#!/usr/bin/env python3
"""Regression tests for the pose-aware camera-GT detector.

Pins detector behaviour on three failure modes that motivated the
pose-discovery layer:

  - session_20260510_121411 — missing first rep (centred-window prominence
    misses the first peak, plus a spurious BOTTOM on the floor).
  - session_20260510_121703 — merge case ("ROM too big": floor→top folded
    into one giant rep).
  - session_20260510_122000 — setup-as-rep case ("ROM too small": tiny
    wobbles between real reps counted as separate reps).

These are smoke tests, not exhaustive — each one asserts a small number
of properties that would break loudly if the pose-aware filtering or the
profile ROM band stopped working. Run from the repo root:

    python3 scripts/test_pose_aware_segmenter.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from rep_segmenter_v2 import (  # noqa: E402  - sys.path adjusted above
    SegConfig,
    clean_marker_signal_v2,
    segment_with_metadata,
)


def _run(session_name: str):
    sess = REPO / "datasets" / "sessions" / session_name
    meta = json.loads((sess / "metadata.json").read_text())
    exercise = meta.get("exercise", "unknown")
    cfg = SegConfig()
    sig = clean_marker_signal_v2(
        sess / "camera" / "marker_positions.csv", exercise, cfg, meta
    )
    assert sig is not None, f"{session_name}: failed to clean signal"
    reps, meta_info = segment_with_metadata(sig, meta, cfg)
    accepted = [r for r in reps if r.gates and r.gates.all_pass]
    pose = (meta_info.get("sets") or [meta_info])[0].get("pose_report") if isinstance(
        (meta_info.get("sets") or [meta_info])[0], dict
    ) else None
    return sig, reps, accepted, pose, meta_info


def test_missing_first_rep_recovered():
    """121411: bicep curl, 7 reps. Detector must recover the first lift
    (originally lost to a 1.8 mm prominence shortfall + a spurious floor
    BOTTOM at t≈3.99 s) and reach ≥ 6 accepted reps."""
    sig, _reps, accepted, pose, _meta = _run("session_20260510_121411")
    t0 = float(sig.t[0])
    assert len(accepted) >= 6, f"expected ≥6 reps, got {len(accepted)}"
    # The recovered first rep concentric must start before t=8.0 s — the
    # baseline detector lost it and the next real rep starts at t≈8.91 s.
    first_conc_rel = accepted[0].t_conc_start - t0
    assert first_conc_rel < 8.0, (
        f"first-rep concentric start at t={first_conc_rel:.2f}s — "
        "the missing-first-cycle synthesis must place it earlier"
    )
    assert pose is not None, "pose_report missing"
    setups = [c for c in pose.get("setup_clusters") or [] if c.get("label") == "setup"]
    assert setups, "floor BOTTOM cluster should have label=setup"
    assert pose.get("rejected_extrema_indices"), (
        "at least one extremum (the floor BOTTOM) must be rejected"
    )


def test_merge_case_rejected():
    """121703: the legacy detector produced one giant 132.6 cm rep when
    the lifter returned the bar to the floor. The profile ROM ceiling
    must reject any rep with ROM above the curl band (0.85 m)."""
    _sig, reps, accepted, _pose, _meta = _run("session_20260510_121703")
    for r in accepted:
        assert r.rom_m < 0.95, f"accepted rep has ROM {r.rom_m * 100:.1f} cm"
    # The merge-rep should appear in the rejected list as a ROM-cap miss
    # OR have been filtered earlier by pose-based extrema rejection.
    rejected = [r for r in reps if not (r.gates and r.gates.all_pass)]
    huge_rejected = [r for r in rejected if r.rom_m > 1.0]
    # Either a huge-rom rep got rejected, or the pose filter killed the
    # whole spurious cycle before it became a candidate.
    if huge_rejected:
        for r in huge_rejected:
            assert r.gates is not None and not r.gates.rom_max_ok, (
                "huge-rom rep must fail rom_max gate"
            )


def test_setup_wobbles_filtered():
    """122000: legacy detector produced 12 reps including 4 tiny-ROM
    "wobbles" (7-14 cm). The new rom_min gate plus working-pose anchoring
    must drop all of them."""
    _sig, reps, accepted, _pose, _meta = _run("session_20260510_122000")
    # Every accepted rep should have ROM in the curl band.
    for r in accepted:
        assert 0.30 <= r.rom_m <= 0.95, (
            f"accepted rep with ROM {r.rom_m * 100:.1f} cm is outside the band"
        )
    # The wobble ROMs (< 0.20 m) must appear only in the rejected list.
    rejected = [r for r in reps if not (r.gates and r.gates.all_pass)]
    wobble_rejected = [r for r in rejected if r.rom_m < 0.20]
    assert wobble_rejected, "no wobble reps were rejected — gating regressed"


def test_squat_does_not_collapse():
    """123358: regression guard. The first version of the pose
    discovery picked a 2-member floor TOP cluster as the working top and
    rejected every real rep. The fixed version must establish a working
    top at standing height (~+0.4 m) with high recurrence."""
    _sig, _reps, accepted, pose, _meta = _run("session_20260511_123358")
    assert len(accepted) >= 10, (
        f"squat collapsed to {len(accepted)} reps — pose discovery must "
        "select the most-populous TOP cluster, not the lowest position"
    )
    assert pose is not None
    wt = pose.get("working_top")
    assert wt is not None and wt["n_members"] >= 5, (
        f"working_top recurrence too low: {wt}"
    )
    assert wt["median_pos"] > 0.0, (
        f"working_top should be at standing height (h > 0), got "
        f"{wt['median_pos']:+.2f} m"
    )


def main():
    tests = [
        test_missing_first_rep_recovered,
        test_merge_case_rejected,
        test_setup_wobbles_filtered,
        test_squat_does_not_collapse,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures.append((t.__name__, str(e)))
            print(f"FAIL  {t.__name__}: {e}")
        else:
            print(f"OK    {t.__name__}")
    print()
    if failures:
        print(f"{len(failures)} of {len(tests)} test(s) failed.")
        raise SystemExit(1)
    print(f"All {len(tests)} test(s) passed.")


if __name__ == "__main__":
    main()
