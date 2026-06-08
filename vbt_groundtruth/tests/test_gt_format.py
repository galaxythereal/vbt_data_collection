"""Step 7 contract test — the studio's ground_truth.json schema is exactly what
metrics/eval.py consumes, and the pipeline prefill exporter is frame-aligned.

The C++ studio writes `ground_truth.json` as a JSON array of these dicts; this test
locks that the array round-trips through eval.match_reps (integer frames, valid
IntervalOutcome statuses) and that the prefill frames live on t = frame_idx/90.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vbt_gt.metrics.eval import match_reps
from vbt_gt.types import IntervalOutcome


def _gt_row(set_id, status, cs, ce, es, ee, rom=0.5, romc=1.0, pause=False, kind=""):
    return {
        "set_id": set_id, "status": status,
        "concentric_start_frame": cs, "concentric_end_frame": ce,
        "eccentric_start_frame": es, "eccentric_end_frame": ee,
        "rom": rom, "rom_completeness": romc,
        "has_pause": pause, "pause_kind": kind, "source": "manual",
    }


def test_ground_truth_array_consumed_by_eval():
    # a hand-authored ground_truth.json (what the studio writes) parses and matches.
    gt = [
        _gt_row(1, IntervalOutcome.COMPLETED_REP.value, 100, 170, 170, 250),
        _gt_row(1, IntervalOutcome.COMPLETED_REP_REDUCED_ROM.value, 300, 360, 360, 440, romc=0.8),
        _gt_row(1, IntervalOutcome.PARTIAL_FAILED.value, 500, 540, None, None, romc=0.5),
        _gt_row(1, IntervalOutcome.TRANSPORT.value, 0, 40, None, None),
    ]
    # serialize + reload like the studio's on-disk file
    gt = json.loads(json.dumps(gt))

    # a perfect predictor (reproduces every row) → all counted + partials match
    pred = json.loads(json.dumps(gt))
    res = match_reps(pred, gt, tol_frames=5)
    assert res["count_gt"] == 2          # two counted (completed + reduced_rom)
    assert res["tp"] == 2 and res["fp"] == 0 and res["fn"] == 0
    assert res["n_gt_partial"] == 1
    assert res["partial_recall"] == 1.0  # the partial matches itself


def test_all_statuses_are_valid_interval_outcomes():
    valid = {o.value for o in IntervalOutcome}
    for o in ("completed_rep", "completed_rep_reduced_rom", "concentric_only",
              "partial_failed", "eccentric_only", "transport", "tracking_invalid"):
        assert o in valid


@pytest.mark.skipif(not Path("datasets/sessions").exists(), reason="no real datasets")
def test_prefill_exporter_is_frame_aligned(tmp_path):
    # run the exporter on one real session and verify schema + t = frame_idx/90.
    sess = next((d for d in sorted(Path("datasets/sessions").glob("session_*"))
                 if (d / "camera" / "marker_positions.csv").exists()), None)
    if sess is None:
        pytest.skip("no camera session")
    r = subprocess.run([sys.executable, "scripts/export_prefill_for_studio.py", str(sess)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dest = Path("vbt_groundtruth/out/prefill") / sess.name
    labels = json.loads((dest / "ground_truth.candidate.json").read_text())
    valid = {o.value for o in IntervalOutcome}
    for lab in labels:
        assert lab["status"] in valid
        assert isinstance(lab["concentric_start_frame"], int)
        assert lab["concentric_end_frame"] > lab["concentric_start_frame"]
    # trace.csv: t_s == frame_idx / 90 exactly, frame_idx contiguous from 0
    import csv
    rows = list(csv.DictReader((dest / "trace.csv").open()))
    assert int(rows[0]["frame_idx"]) == 0
    for i, row in enumerate(rows[:200]):
        assert int(row["frame_idx"]) == i
        assert abs(float(row["t_s"]) - i / 90.0) < 1e-6
    # every label frame lies inside the trace
    nframes = len(rows)
    for lab in labels:
        assert 0 <= lab["concentric_start_frame"] < nframes
