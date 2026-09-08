#!/usr/bin/env python
"""Statistics for the camera-free pipeline, per attitude engine.

Reports what an engineering reader wants (RMSE) and what the velocity-based-training
literature judges validity on (bias with limits of agreement, SEE, r, CV, ICC), and then
answers the question those alone do not: IS THE ERROR A SHIFT OR IS IT RANDOM?

Three separate senses of "a shift", each measured:

  A FIXED bias is one constant added to every repetition in the corpus. Measured as the
    mean difference with a 95% confidence interval; it is real if the interval excludes
    zero. This is the part a single calibration constant would remove.

  A PROPORTIONAL bias is an error that grows with the quantity, i.e. a gain error rather
    than an offset. Measured by regressing the difference on the mean of the two methods
    -- the Bland-Altman regression the field uses -- and testing the slope against zero.

  A PER-SESSION shift is one constant per session rather than one for the corpus: a
    remount, a different bar, a different day. Measured by splitting the variance of the
    differences into a between-session and a within-session part with a one-way
    random-effects model, and by reporting what the RMSE would become if each session's
    own mean difference were removed. That last number is the practical one: it is what a
    per-session calibration would buy, and it is an upper bound on what any per-session
    correction can achieve.

Everything is computed on the repetitions the camera-free pipeline FOUND and matched, and
the matched fraction is reported, because a repetition that was never found contributes to
no error figure and pretending otherwise would flatter the result.

    .venv/bin/python scripts/imu/pipeline_stats.py [--n 84] [--engines vqf eskf2]
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent/"reference"))
import imu_full_pipeline as F
import pipeline as P
import rts


# ------------------------------------------------------------------- statistics helpers

def see(x, y):
    """Standard error of the estimate: the criterion y regressed on the measure x.

    SEE = sqrt(sum (y - yhat)^2 / (n-2)). This is the definition the velocity-based
    training validity papers use, and it is NOT the same as the RMS difference: it is the
    scatter that survives after a best-fit line, so it excludes both the fixed and the
    proportional bias.
    """
    n = len(x)
    if n < 3: return float("nan")
    A = np.column_stack([x, np.ones(n)])
    b = np.linalg.lstsq(A, y, rcond=None)[0]
    r = y - A @ b
    return float(np.sqrt(np.sum(r*r)/(n-2)))


def icc_a1(a, b):
    """ICC(A,1), two-way random, absolute agreement -- agreement between two methods."""
    n = len(a)
    if n < 3: return float("nan")
    X = np.column_stack([a, b]); k = 2
    grand = X.mean()
    row = X.mean(axis=1); colm = X.mean(axis=0)
    MSR = k*np.sum((row-grand)**2)/(n-1)
    MSC = n*np.sum((colm-grand)**2)/(k-1)
    SSE = np.sum((X - row[:, None] - colm[None, :] + grand)**2)
    MSE = SSE/((n-1)*(k-1))
    den = MSR + (k-1)*MSE + k*(MSC-MSE)/n
    return float((MSR-MSE)/den) if den else float("nan")


def ba_regression(d, m):
    """Bland-Altman regression of the difference on the mean: slope, its 95% CI, and p."""
    n = len(d)
    if n < 4: return (float("nan"),)*4
    A = np.column_stack([m, np.ones(n)])
    beta, *_ = np.linalg.lstsq(A, d, rcond=None)
    res = d - A @ beta
    s2 = float(np.sum(res*res)/(n-2))
    cov = s2*np.linalg.inv(A.T @ A)
    sl, sl_se = float(beta[0]), float(np.sqrt(cov[0, 0]))
    t = sl/sl_se if sl_se else 0.0
    # normal approximation for p; n is in the hundreds here so it is indistinguishable
    from math import erfc, sqrt
    p = erfc(abs(t)/sqrt(2.0))
    return sl, sl-1.96*sl_se, sl+1.96*sl_se, p


def variance_split(d, groups):
    """One-way random effects: between-group and within-group variance of the differences.

    Returns (sigma2_between, sigma2_within, fraction_between, rmse_after_group_centring).
    """
    d = np.asarray(d, float); groups = np.asarray(groups)
    ks = [d[groups == g] for g in np.unique(groups)]
    ks = [k for k in ks if len(k) >= 1]
    a = len(ks)
    if a < 2: return (float("nan"),)*4
    N = sum(len(k) for k in ks)
    grand = d.mean()
    SSB = sum(len(k)*(k.mean()-grand)**2 for k in ks)
    SSW = sum(np.sum((k-k.mean())**2) for k in ks)
    MSB = SSB/(a-1)
    MSW = SSW/(N-a) if N > a else float("nan")
    n_bar = N/a
    s2b = max((MSB-MSW)/n_bar, 0.0)
    s2w = MSW
    frac = s2b/(s2b+s2w) if (s2b+s2w) else float("nan")
    centred = np.concatenate([k-k.mean() for k in ks])
    return s2b, s2w, frac, float(np.sqrt(np.mean(centred**2)))


def report_pair(name, imu, cam, sess, unit, scale=1000.0):
    """Everything about one paired quantity."""
    imu = np.asarray(imu, float)*scale
    cam = np.asarray(cam, float)*scale
    d = imu - cam
    m = 0.5*(imu+cam)
    n = len(d)
    if n < 4:
        print(f"  {name}: only {n} pairs"); return
    bias = d.mean(); sd = d.std(ddof=1)
    ci = 1.96*sd/np.sqrt(n)
    rmse = np.sqrt(np.mean(d*d))
    r = float(np.corrcoef(imu, cam)[0, 1])
    s = see(imu, cam)
    te = sd/np.sqrt(2.0)
    cv = 100*te/abs(m.mean()) if m.mean() else float("nan")
    sl, lo, hi, p = ba_regression(d, m)
    s2b, s2w, frac, rmse_c = variance_split(d, sess)

    print(f"\n  {name}   n = {n}")
    print(f"    RMSE                    {rmse:8.2f} {unit}")
    print(f"    SEE                     {s:8.2f} {unit}    "
          f"(scatter after a best-fit line)")
    print(f"    bias                    {bias:+8.2f} {unit}    "
          f"95% CI [{bias-ci:+.2f}, {bias+ci:+.2f}]  -> "
          f"{'REAL, excludes zero' if abs(bias) > ci else 'not distinguishable from zero'}")
    print(f"    SD of differences       {sd:8.2f} {unit}")
    print(f"    95% limits of agreement {bias-1.96*sd:+8.2f} to {bias+1.96*sd:+.2f} {unit}")
    print(f"    typical error           {te:8.2f} {unit}    CV {cv:5.2f}%")
    print(f"    r                       {r:8.4f}      R2 {r*r:6.4f}      "
          f"ICC(A,1) {icc_a1(imu, cam):6.4f}")
    print(f"    fixed vs random         "
          f"{100*bias**2/(bias**2+sd**2):5.1f}% of the mean square is the fixed bias, "
          f"{100*sd**2/(bias**2+sd**2):.1f}% is scatter")
    print(f"    proportional bias       slope {sl:+.5f} per {unit} "
          f"[{lo:+.5f}, {hi:+.5f}]  p = {p:.3g}  -> "
          f"{'PRESENT' if p < 0.05 else 'absent'}")
    print(f"    per-session shift       between-session SD {np.sqrt(s2b):6.2f} {unit}, "
          f"within-session SD {np.sqrt(s2w):6.2f}   "
          f"({100*frac:.0f}% of the variance is between sessions)")
    print(f"    RMSE if each session's own mean were removed: {rmse_c:6.2f} {unit}   "
          f"({100*(1-rmse_c/rmse):+.0f}% against {rmse:.2f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--engines", nargs="*", default=["vqf", "eskf2"])
    ap.add_argument("--tol", type=int, default=45)
    args = ap.parse_args()

    load = {}
    gt = P.DS/"ground_truth.csv"
    if gt.exists():
        for row in csv.DictReader(gt.open()):
            try: load[row["session_id"]] = float(row["load_kg"])
            except (KeyError, ValueError): pass

    for eng in args.engines:
        pk_i = pk_c = mn_i = mn_c = rm_i = rm_c = None
        pk_i, pk_c, mn_i, mn_c, rm_i, rm_c = [], [], [], [], [], []
        traj, sess, exs, lds = [], [], [], []
        n_cam = n_live = n_off = n_sess = ex_live = ex_off = 0
        cnt_err, matched, missed, extra = [], 0, 0, 0

        for d in sorted(P.DS.glob("session_*"))[:args.n]:
            try:
                ex = json.loads((d/"metadata.json").read_text())["exercise"]
                truth, cam_p, cam_v = F.camera_truth(d)
                if not truth: continue
                tr = F.imu_frame_track(d, eng)
                if tr is None: continue
                on, _, _, _ = F.run_online(tr["p"], tr["dt"], ex)
                meta, off, sd_m = F.run_offline(tr["p"], tr["dt"], ex, on)
                if meta is None: continue
                xs, _ = rts.smooth(tr["p"], None, dt=tr["dt"], meas_sd=sd_m)
                imu_p, imu_v = xs[:, 0], xs[:, 1]

                n_sess += 1
                n_cam += len(truth); n_live += len(on); n_off += len(off)
                ex_live += int(len(on) == len(truth)); ex_off += int(len(off) == len(truth))
                cnt_err.append(len(off) - len(truth))
                pairs = F.match([r["concentric_end_frame"] for r in off],
                                [r["concentric_end_frame"] for r in truth], args.tol)
                matched += len(pairs)
                missed += len(truth) - len(pairs)
                extra += len(off) - len(pairs)
                for i, j, _ in pairs:
                    mi, tj = off[i], truth[j]
                    cs, ce = tj["concentric_start_frame"], tj["concentric_end_frame"]
                    if not (0 <= cs < ce < len(cam_v)): continue
                    pk_i.append(mi["peak_velocity"])
                    pk_c.append(float(np.max(np.abs(cam_v[cs:ce+1]))))
                    mn_i.append(mi["mean_velocity"])
                    mn_c.append(float(np.mean(np.abs(cam_v[cs:ce+1]))))
                    rm_i.append(mi["rom_m"])
                    rm_c.append(abs(float(cam_p[ce]-cam_p[cs])))
                    # HEIGHT over the repetition, each track referred to its own value at
                    # the repetition start -- absolute height is not observable
                    a_ = min(tj["concentric_start_frame"], tj["eccentric_start_frame"])
                    b_ = max(tj["concentric_end_frame"], tj["eccentric_end_frame"])
                    if 0 <= a_ < b_ < min(len(cam_p), len(imu_p)):
                        ci = imu_p[a_:b_+1] - imu_p[a_]
                        cc = cam_p[a_:b_+1] - cam_p[a_]
                        traj.append(float(np.sqrt(np.mean((ci-cc)**2))))
                    sess.append(d.name); exs.append(ex)
                    lds.append(load.get(d.name, float("nan")))
            except Exception as e:
                print(f"  skip {d.name}: {e}", file=sys.stderr)

        lab = {"vqf": "VQF", "eskf2": "ESKF", "eskf": "ESKF"}.get(eng, eng.upper())
        print(f"\n{'='*78}\n{lab}   {n_sess} sessions, camera-free pipeline\n{'='*78}")
        ce = np.array(cnt_err)
        print("REPETITION COUNTS")
        print(f"  camera                  {n_cam:>6}")
        print(f"  live annotator          {n_live:>6}  ({n_live-n_cam:+d}, "
              f"{100*n_live/n_cam:5.1f}%)   {ex_live}/{n_sess} sessions exact")
        print(f"  post-session annotator  {n_off:>6}  ({n_off-n_cam:+d}, "
              f"{100*n_off/n_cam:5.1f}%)   {ex_off}/{n_sess} sessions exact")
        print(f"  per-session count error: mean {ce.mean():+.2f}, SD {ce.std(ddof=1):.2f}, "
              f"range {ce.min():+d} to {ce.max():+d}")
        print(f"    within 0 {100*np.mean(ce==0):5.1f}%   within 1 "
              f"{100*np.mean(np.abs(ce)<=1):5.1f}%   within 2 "
              f"{100*np.mean(np.abs(ce)<=2):5.1f}%")
        prec = matched/(matched+extra) if matched+extra else float("nan")
        rec = matched/(matched+missed) if matched+missed else float("nan")
        print(f"  matched {matched}, missed {missed}, extra {extra}   "
              f"precision {prec:.4f}, recall {rec:.4f}, "
              f"F1 {2*prec*rec/(prec+rec):.4f}")
        print(f"  matched fraction of the camera's repetitions: "
              f"{100*matched/n_cam:.1f}%  -- every figure below is on these")

        print("\nVELOCITY")
        report_pair("peak concentric velocity", pk_i, pk_c, sess, "mm/s")
        report_pair("mean concentric velocity", mn_i, mn_c, sess, "mm/s")
        print("\nPOSITION")
        report_pair("range of motion", rm_i, rm_c, sess, "mm")
        t = np.array(traj)*1000
        if len(t):
            print(f"\n  height over the repetition, RMS within each repetition   n = {len(t)}")
            print(f"    median {np.median(t):8.2f} mm    90th {np.percentile(t,90):8.2f}"
                  f"    RMS over repetitions {np.sqrt(np.mean(t*t)):8.2f}")

        print("\nPER EXERCISE, peak concentric velocity")
        A = np.array(pk_i)*1000; B = np.array(pk_c)*1000; E = np.array(exs)
        print(f"  {'exercise':<14}{'n':>5}{'RMSE':>9}{'bias':>9}{'SD':>8}{'SEE':>8}{'r':>8}")
        for e in sorted(set(exs)):
            k = E == e
            dd = A[k]-B[k]
            print(f"  {e:<14}{k.sum():>5}{np.sqrt(np.mean(dd*dd)):>9.1f}"
                  f"{dd.mean():>+9.1f}{dd.std(ddof=1):>8.1f}{see(A[k],B[k]):>8.1f}"
                  f"{np.corrcoef(A[k],B[k])[0,1]:>8.3f}")

        L = np.array(lds); ok = np.isfinite(L)
        if ok.sum() > 40:
            print("\nBY LOAD, peak concentric velocity "
                  "(the field judges validity per relative load)")
            qs = np.quantile(L[ok], [0, .25, .5, .75, 1.0])
            print(f"  {'load band':<16}{'n':>5}{'RMSE':>9}{'bias':>9}{'SD':>8}{'r':>8}")
            for i in range(4):
                k = ok & (L >= qs[i]) & (L <= qs[i+1] if i == 3 else L < qs[i+1])
                if k.sum() < 10: continue
                dd = A[k]-B[k]
                print(f"  {qs[i]:>5.0f}-{qs[i+1]:<10.0f}{k.sum():>5}"
                      f"{np.sqrt(np.mean(dd*dd)):>9.1f}{dd.mean():>+9.1f}"
                      f"{dd.std(ddof=1):>8.1f}{np.corrcoef(A[k],B[k])[0,1]:>8.3f}")


if __name__ == "__main__":
    main()
