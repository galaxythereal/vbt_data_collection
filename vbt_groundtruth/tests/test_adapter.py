"""M0.2 — real-format adapter rules: meta allowlist + required/validated frame timeline."""

import json

import numpy as np
import pandas as pd
import pytest

from vbt_gt.io.adapter import to_raw_session
from vbt_gt.types import Exercise


def _write_session(
    tmp_path,
    n=20,
    exercise="deadlift",
    target_reps=5,
    intended_reps=None,
    extra_md=None,
    detected=None,
    omit_video_frames=False,
    drop_frame_idx_col=False,
    vf_rows=None,
    frame_idx=None,
):
    sd = tmp_path / "session_x"
    (sd / "camera").mkdir(parents=True, exist_ok=True)

    det = np.ones(n, dtype=int) if detected is None else np.asarray(detected, dtype=int)
    marker = pd.DataFrame({
        "timestamp_s": np.arange(n) / 90.0 + 1e9,
        "x_m": np.linspace(0.0, 0.10, n),
        "y_m": np.linspace(0.0, 0.50, n),
        "z_m": 2.40 * np.ones(n),
        "pixel_u": np.zeros(n), "pixel_v": np.zeros(n),
        "confidence": 0.70 * np.ones(n), "snr": np.ones(n),
        "circularity": 0.90 * np.ones(n),
        "depth_source": ["depth"] * n, "detected": det,
    })
    marker.to_csv(sd / "camera" / "marker_positions.csv", index=False)

    if not omit_video_frames:
        rows = n if vf_rows is None else vf_rows
        fi = np.arange(rows) if frame_idx is None else np.asarray(frame_idx)
        vf = pd.DataFrame({
            "frame_idx": fi,
            "host_timestamp_s": np.arange(rows) / 90.0,
            "hw_timestamp_s": np.arange(rows) / 90.0 + 1e9,
            "unified_time_s": np.arange(rows) / 90.0 + 1e9,
            "frame_number": np.arange(rows) + 100,
        })
        if drop_frame_idx_col:
            vf = vf.drop(columns=["frame_idx"])
        vf.to_csv(sd / "camera" / "video_frames.csv", index=False)

    md = {"session_id": "session_x", "exercise": exercise, "target_reps": target_reps}
    if intended_reps is not None:
        md["sets"] = [{"set_id": 1, "intended_reps": intended_reps}]
    if extra_md:
        md.update(extra_md)
    (sd / "metadata.json").write_text(json.dumps(md))
    return sd


# ───────────────────────── Rule 1: meta allowlist ─────────────────────────

def test_meta_only_target_and_intended(tmp_path):
    sd = _write_session(
        tmp_path, target_reps=5, intended_reps=8,
        extra_md={"completed_reps": 99, "actual_reps": 7, "subject_name": "x"},
    )
    raw = to_raw_session(sd)
    assert set(raw.meta) <= {"target_reps", "intended_reps"}
    assert raw.meta["target_reps"] == 5
    assert raw.meta["intended_reps"] == 8
    for banned in ("completed_reps", "actual_reps", "subject_name"):
        assert banned not in raw.meta


def test_meta_drops_arbitrary_caller_keys(tmp_path):
    sd = _write_session(tmp_path, target_reps=5)
    raw = to_raw_session(
        sd, meta={"athlete_id": "S46", "target_reps": 3, "completed_reps": 12})
    # only allowlisted keys survive; caller may override target_reps
    assert set(raw.meta) <= {"target_reps", "intended_reps"}
    assert raw.meta["target_reps"] == 3
    assert "athlete_id" not in raw.meta
    assert "completed_reps" not in raw.meta


def test_meta_only_target_when_no_intended(tmp_path):
    sd = _write_session(tmp_path, target_reps=5, intended_reps=None)
    raw = to_raw_session(sd)
    assert raw.meta == {"target_reps": 5}


# ──────────────────── Rule 2: required / validated frame timeline ────────────────────

def test_requires_video_frames(tmp_path):
    sd = _write_session(tmp_path, omit_video_frames=True)
    with pytest.raises(ValueError, match="video_frames"):
        to_raw_session(sd)


def test_requires_frame_idx_column(tmp_path):
    sd = _write_session(tmp_path, drop_frame_idx_col=True)
    with pytest.raises(ValueError, match="frame_idx"):
        to_raw_session(sd)


def test_row_count_mismatch_raises(tmp_path):
    sd = _write_session(tmp_path, n=20, vf_rows=19)
    with pytest.raises(ValueError, match="!="):
        to_raw_session(sd)


def test_noncontiguous_frame_idx_raises(tmp_path):
    sd = _write_session(tmp_path, n=20, frame_idx=np.arange(20) + 5)  # 5..24
    with pytest.raises(ValueError, match="contiguous"):
        to_raw_session(sd)


def test_no_silent_arange_fallback(tmp_path):
    # gap in frame_idx (not 0..N-1) must NOT be silently replaced by arange
    fi = np.arange(20)
    fi[10:] += 3  # introduce a gap
    sd = _write_session(tmp_path, n=20, frame_idx=fi)
    with pytest.raises(ValueError):
        to_raw_session(sd)


# ──────────────────── happy path: t = frame_idx/90, dropouts → NaN ────────────────────

def test_happy_path_time_and_nan(tmp_path):
    det = np.ones(20, dtype=int)
    det[5:8] = 0
    sd = _write_session(tmp_path, n=20, detected=det, exercise="bench_press")
    raw = to_raw_session(sd)
    assert raw.exercise == Exercise.BENCH
    np.testing.assert_allclose(raw.t, np.arange(20) / 90.0)
    assert np.isnan(raw.xyz[5:8]).all()
    assert np.isfinite(raw.xyz[:5]).all() and np.isfinite(raw.xyz[8:]).all()
