#!/usr/bin/env python3
"""Corpus-level EDA and annotation audit for collected VBT sessions.

Outputs:
  - datasets/sessions/eda_summary.json
  - datasets/sessions/eda_sessions.csv
  - datasets/sessions/eda_report.md
  - annotations/rep_segments.candidate.json for finalized sessions whose
    rep_segments.json is empty or missing, when --write-candidates is passed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

from evaluate_rep_segmentation import DEFAULT, clean_marker_signal, extrema, score, segment


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def safe_float(v, default=math.nan):
    try:
        return float(v)
    except Exception:
        return default


def csv_stats(path: Path, time_col: str | None = None) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {"present": False, "rows": 0}
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return {"present": True, "rows": 0, "error": str(e)}
    out = {"present": True, "rows": int(len(df))}
    if time_col and time_col in df and len(df) >= 2:
        t = pd.to_numeric(df[time_col], errors="coerce").to_numpy(float)
        t = t[np.isfinite(t)]
        if len(t) >= 2:
            dt = np.diff(t)
            dt = dt[np.isfinite(dt) & (dt > 0)]
            duration = float(t[-1] - t[0])
            out["duration_s"] = duration
            out["rate_hz"] = float(len(t) / duration) if duration > 0 else 0.0
            out["jitter_ms"] = float(np.std(dt) * 1000.0) if len(dt) else 0.0
            out["gaps_gt_20ms"] = int(np.sum(dt > 0.020)) if len(dt) else 0
    for col in ("detected", "confidence", "snr", "circularity"):
        if col in df:
            vals = pd.to_numeric(df[col], errors="coerce")
            out[f"{col}_mean"] = float(vals.mean()) if len(vals) else math.nan
    if "fsync_flag" in df:
        vals = pd.to_numeric(df["fsync_flag"], errors="coerce").fillna(0)
        out["fsync_hits"] = int((vals != 0).sum())
    return out


def annotation_issues(reps: list) -> list[str]:
    issues: list[str] = []
    if not isinstance(reps, list):
        return ["rep_segments.json is not a list"]
    sorted_reps = sorted(reps, key=lambda r: safe_float(r.get("concentric", {}).get("t_start")))
    for i, r in enumerate(sorted_reps):
        prefix = f"R{r.get('rep_id', i + 1)}"
        for name in ("concentric", "top_rest", "eccentric", "rest"):
            seg = r.get(name)
            if not isinstance(seg, dict):
                issues.append(f"{prefix} missing {name}")
                continue
            a = safe_float(seg.get("t_start"))
            b = safe_float(seg.get("t_end"))
            if not (math.isfinite(a) and math.isfinite(b)):
                issues.append(f"{prefix} {name} has non-finite boundary")
            elif b < a:
                issues.append(f"{prefix} {name} ends before it starts")
        c = r.get("concentric", {})
        tr = r.get("top_rest", {})
        e = r.get("eccentric", {})
        rest = r.get("rest", {})
        chain = [
            safe_float(c.get("t_start")),
            safe_float(c.get("t_end")),
            safe_float(tr.get("t_start")),
            safe_float(tr.get("t_end")),
            safe_float(e.get("t_start")),
            safe_float(e.get("t_end")),
            safe_float(rest.get("t_start")),
            safe_float(rest.get("t_end")),
        ]
        if all(math.isfinite(x) for x in chain):
            if abs(chain[1] - chain[2]) > 1e-6:
                issues.append(f"{prefix} top_rest.t_start != concentric.t_end")
            if abs(chain[3] - chain[4]) > 1e-6:
                issues.append(f"{prefix} eccentric.t_start != top_rest.t_end")
            if abs(chain[5] - chain[6]) > 1e-6:
                issues.append(f"{prefix} rest.t_start != eccentric.t_end")
            if any(chain[j + 1] < chain[j] for j in range(len(chain) - 1)):
                issues.append(f"{prefix} phase boundaries out of order")
        rom = safe_float(r.get("rom_m"))
        if math.isfinite(rom) and rom < 0.05:
            issues.append(f"{prefix} ROM below 50 mm")
        if math.isfinite(rom) and rom > 1.5:
            issues.append(f"{prefix} ROM above 1.5 m")
        if i:
            prev_end = safe_float(sorted_reps[i - 1].get("rest", {}).get("t_end"))
            cur_start = safe_float(c.get("t_start"))
            if math.isfinite(prev_end) and math.isfinite(cur_start) and cur_start < prev_end:
                issues.append(f"{prefix} overlaps previous rep")
    return issues


def candidate_reps_from_signal(sig, cfg=DEFAULT) -> list[dict]:
    t, pos, vel = sig
    prom = max(0.005, cfg["min_rep_displacement_m"] * cfg["prominence_fraction"])
    exts = extrema(t, pos, cfg["peak_window_s"], prom)
    out = []
    seed = midpoint = None
    last_type, last_t = None, -1.0
    for typ, et, ep, idx in exts:
        if et - t[0] < cfg["setup_ignore_s"]:
            continue
        if last_type == typ and et - last_t < 0.30:
            continue
        if seed is None:
            if typ != "BOTTOM":
                last_type, last_t = typ, et
                continue
            seed, midpoint, last_type, last_t = (typ, et, ep, idx), None, typ, et
            continue
        if midpoint is None and typ == "TOP":
            midpoint, last_type, last_t = (typ, et, ep, idx), typ, et
            continue
        if midpoint is None and typ == "BOTTOM":
            seed, last_type, last_t = (typ, et, ep, idx), typ, et
            continue
        if typ != "BOTTOM":
            last_type, last_t = typ, et
            continue

        _, st, sp, _ = seed
        _, mt, mp, _ = midpoint
        conc_start, conc_end = st, mt
        ecc_start, ecc_end = mt, et
        rep_end = et
        duration = et - st
        rom = abs(mp - sp)
        c_lo = int(np.searchsorted(t, conc_start))
        c_hi = max(c_lo + 1, int(np.searchsorted(t, conc_end)))
        c_vel = vel[c_lo:c_hi]
        peak = float(np.max(c_vel)) if len(c_vel) else 0.0
        avg = float(np.mean(c_vel)) if len(c_vel) else 0.0
        full_pass = (
            duration >= cfg["min_rep_duration_s"]
            and rom >= cfg["min_rep_displacement_m"]
            and peak >= cfg["min_concentric_peak_mps"]
        )
        if not full_pass:
            seed, midpoint, last_type, last_t = (typ, et, ep, idx), None, typ, et
            continue
        out.append(
            {
                "rep_id": len(out) + 1,
                "set_id": 1,
                "concentric": {
                    "t_start": float(conc_start),
                    "t_end": float(conc_end),
                    "peak_vel": peak,
                    "source": "auto_candidate",
                },
                "top_rest": {
                    "t_start": float(conc_end),
                    "t_end": float(ecc_start),
                    "source": "auto_candidate",
                },
                "eccentric": {
                    "t_start": float(ecc_start),
                    "t_end": float(ecc_end),
                    "source": "auto_candidate",
                },
                "rest": {
                    "t_start": float(rep_end),
                    "t_end": float(rep_end),
                    "source": "auto_candidate",
                },
                "mean_concentric_velocity": avg,
                "peak_concentric_velocity": peak,
                "rom_m": float(rom),
                "confidence": 1.0,
            }
        )
        seed, midpoint, last_type, last_t = (typ, et, ep, idx), None, typ, et
    return out


def summarize_reps(reps: list) -> dict:
    if not isinstance(reps, list) or not reps:
        return {"count": 0}
    pvs = [safe_float(r.get("peak_concentric_velocity")) for r in reps]
    roms = [safe_float(r.get("rom_m")) for r in reps]
    durs = [
        safe_float(r.get("concentric", {}).get("t_end")) - safe_float(r.get("concentric", {}).get("t_start"))
        for r in reps
    ]
    pvs = [x for x in pvs if math.isfinite(x)]
    roms = [x for x in roms if math.isfinite(x)]
    durs = [x for x in durs if math.isfinite(x)]
    return {
        "count": len(reps),
        "pv_mean": mean(pvs) if pvs else math.nan,
        "pv_median": median(pvs) if pvs else math.nan,
        "rom_mean": mean(roms) if roms else math.nan,
        "rom_median": median(roms) if roms else math.nan,
        "conc_dur_mean": mean(durs) if durs else math.nan,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    ap.add_argument("--write-candidates", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    rows: list[dict] = []
    for sess in sorted(p for p in root.glob("session_*") if p.is_dir()):
        partial = sess.name.endswith(".partial")
        meta = read_json(sess / "metadata.json", {})
        exercise = meta.get("exercise", "unknown")
        ann_path = sess / "annotations" / "rep_segments.json"
        reps = read_json(ann_path, None) if ann_path.exists() else None
        ann_status = "missing" if reps is None else ("empty" if reps == [] else "annotated")
        issues = [] if reps is None else annotation_issues(reps)
        marker = csv_stats(sess / "camera" / "marker_positions.csv", "timestamp_s")
        video = csv_stats(sess / "camera" / "video_frames.csv", "host_timestamp_s")
        imu = csv_stats(sess / "imu" / "raw_imu.csv", "unified_time_s")
        rep_summary = summarize_reps(reps if isinstance(reps, list) else [])

        pred_count = math.nan
        score_f1 = math.nan
        pred_reps = []
        sig = None
        if marker.get("present") and meta.get("exercise"):
            try:
                sig = clean_marker_signal(sess / "camera" / "marker_positions.csv", str(exercise))
                if sig is not None:
                    pred_reps = segment(*sig, DEFAULT)
                    pred_count = len(pred_reps)
                    if isinstance(reps, list):
                        _, _, _, score_f1 = score(pred_reps, reps)
            except Exception:
                pass

        if args.write_candidates and not partial and ann_status in {"missing", "empty"} and sig is not None:
            candidate = candidate_reps_from_signal(sig, DEFAULT)
            out_path = sess / "annotations" / "rep_segments.candidate.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(candidate, indent=2) + "\n")

        rows.append(
            {
                "session": sess.name,
                "partial": partial,
                "exercise": exercise,
                "annotation_status": ann_status,
                "annotation_count": rep_summary["count"],
                "predicted_count": pred_count,
                "pred_vs_annotation_f1": score_f1,
                "annotation_issue_count": len(issues),
                "annotation_issues": "; ".join(issues[:8]),
                "marker_rows": marker.get("rows", 0),
                "marker_rate_hz": marker.get("rate_hz", math.nan),
                "marker_detected_pct": marker.get("detected_mean", math.nan) * 100.0
                if math.isfinite(marker.get("detected_mean", math.nan))
                else math.nan,
                "marker_confidence": marker.get("confidence_mean", math.nan),
                "marker_snr": marker.get("snr_mean", math.nan),
                "video_frames": video.get("rows", 0),
                "video_rate_hz": video.get("rate_hz", math.nan),
                "imu_rows": imu.get("rows", 0),
                "imu_rate_hz": imu.get("rate_hz", math.nan),
                "fsync_hits": imu.get("fsync_hits", math.nan),
                "pv_mean": rep_summary.get("pv_mean", math.nan),
                "rom_mean": rep_summary.get("rom_mean", math.nan),
                "conc_dur_mean": rep_summary.get("conc_dur_mean", math.nan),
            }
        )

    finalized = [r for r in rows if not r["partial"]]
    by_ex = defaultdict(list)
    for r in rows:
        by_ex[r["exercise"]].append(r)
    status_counts = Counter(r["annotation_status"] for r in rows)
    summary = {
        "root": str(root),
        "sessions_total": len(rows),
        "sessions_finalized": len(finalized),
        "sessions_partial": len(rows) - len(finalized),
        "annotation_status_counts": dict(status_counts),
        "total_annotated_reps": int(sum(r["annotation_count"] for r in rows)),
        "total_predicted_reps": int(sum(r["predicted_count"] for r in rows if math.isfinite(r["predicted_count"]))),
        "by_exercise": {
            ex: {
                "sessions": len(items),
                "finalized": sum(not x["partial"] for x in items),
                "annotated_reps": int(sum(x["annotation_count"] for x in items)),
                "predicted_reps": int(sum(x["predicted_count"] for x in items if math.isfinite(x["predicted_count"]))),
            }
            for ex, items in sorted(by_ex.items())
        },
        "needs_annotation": [
            r["session"]
            for r in finalized
            if r["annotation_status"] in {"missing", "empty"} and r["predicted_count"] > 0
        ],
        "low_agreement": [
            {
                "session": r["session"],
                "exercise": r["exercise"],
                "annotation_count": r["annotation_count"],
                "predicted_count": r["predicted_count"],
                "f1": r["pred_vs_annotation_f1"],
            }
            for r in finalized
            if math.isfinite(r["pred_vs_annotation_f1"]) and r["annotation_count"] > 0 and r["pred_vs_annotation_f1"] < 0.95
        ],
    }

    json_path = root / "eda_summary.json"
    csv_path = root / "eda_sessions.csv"
    md_path = root / "eda_report.md"
    json_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Sessions EDA and Annotation Audit",
        "",
        f"- Root: `{root}`",
        f"- Sessions: {summary['sessions_total']} total, {summary['sessions_finalized']} finalized, {summary['sessions_partial']} partial",
        f"- Annotation status: {dict(status_counts)}",
        f"- Annotated reps: {summary['total_annotated_reps']}",
        f"- Detector candidate reps: {summary['total_predicted_reps']}",
        "",
        "## By Exercise",
        "",
        "| exercise | sessions | finalized | annotated reps | candidate reps |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for ex, s in summary["by_exercise"].items():
        lines.append(f"| {ex} | {s['sessions']} | {s['finalized']} | {s['annotated_reps']} | {s['predicted_reps']} |")
    lines += [
        "",
        "## Finalized Sessions Still Needing Human Truth Review",
        "",
    ]
    for name in summary["needs_annotation"]:
        r = next(x for x in rows if x["session"] == name)
        lines.append(f"- `{name}`: {r['exercise']}, current={r['annotation_count']}, candidate={int(r['predicted_count'])}")
    lines += [
        "",
        "## Annotated Sessions With Detector Agreement Below 0.95",
        "",
    ]
    for item in summary["low_agreement"]:
        lines.append(
            f"- `{item['session']}`: {item['exercise']}, annotations={item['annotation_count']}, "
            f"candidate={int(item['predicted_count'])}, F1={item['f1']:.3f}"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Candidate annotations are signal-derived review aids, not ground truth.",
        "- Reaching 100% true annotation still requires reviewing the flagged sessions in the annotation studio against video/signal.",
    ]
    md_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    if args.write_candidates:
        print("Wrote candidate annotation files for empty/missing finalized sessions.")


if __name__ == "__main__":
    main()
