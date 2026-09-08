#!/usr/bin/env python
"""Allan deviation of this sensor, from the corpus's own still periods.

WHY. The ESKF's process noise was set by hand: sigma_g = 0.05 deg/s and sigma_b =
0.002 deg/s. Neither came from a measurement. Several of the research reports said the same
thing in different words -- use the actual Allan values -- and the paper has carried an
Allan-variance section marked "planned" since it was written. This measures it, from the
still windows between sets, across all 84 sessions.

A filter whose process noise is wrong by an order of magnitude is not a filter that has been
tuned; it is a filter that happens to work. If sigma_g is far too large the filter distrusts
the gyroscope and leans on an accelerometer that is being shaken by the lift, which is
exactly the failure the low-passed vertical reference was introduced to avoid.

WHAT IS COMPUTED. The overlapping Allan deviation sigma(tau) for each axis, then the three
standard coefficients read off it:

    sigma(tau)^2  =  N^2/tau  +  2 B^2 ln 2 / pi  +  K^2 tau / 3

  N  angle (or velocity) random walk -- the white-noise density, the tau^-1/2 slope.
     This is the sigma_g the filter's Q needs.
  B  bias instability -- the floor of the curve, at 0.664 sigma_min.
  K  rate random walk -- the tau^+1/2 slope, which is what sigma_b models.

Still windows are found in the sensor's own angular rate, never assumed from a position in
the session, for the same reason the calibration does it that way: the period before the
first repetition is not still, the bar is being handled.

    .venv/bin/python scripts/imu/noise_characterisation.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P


def allan(x, dt, taus):
    """Overlapping Allan deviation of a rate signal x sampled at dt."""
    n = len(x)
    theta = np.cumsum(x)*dt                      # integrate rate to angle
    out = np.full(len(taus), np.nan)
    for i, tau in enumerate(taus):
        m = int(round(tau/dt))
        if m < 1 or n < 3*m: continue
        d = theta[2*m:] - 2*theta[m:-m] + theta[:-2*m]
        out[i] = np.sqrt(np.sum(d*d)/(2*tau**2*len(d)))
    return out


def fit_coeffs(taus, sig):
    """N from the tau^-1/2 region, B from the floor, K from the tau^+1/2 region."""
    ok = np.isfinite(sig)
    t, s = taus[ok], sig[ok]
    if len(t) < 6: return (np.nan,)*3
    # N: the shortest decade, where white noise dominates
    lo = t <= t[0]*10
    N = float(np.median(s[lo]*np.sqrt(t[lo]))) if lo.sum() >= 3 else np.nan
    # B: the minimum of the curve
    B = float(s.min()/0.664)
    # K: the longest decade, if the curve has turned up
    hi = t >= t[-1]/10
    K = float(np.median(s[hi]*np.sqrt(3.0/t[hi]))) if hi.sum() >= 3 else np.nan
    return N, B, K


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--min-still", type=float, default=3.0,
                    help="shortest still stretch to use, seconds")
    args = ap.parse_args()

    taus = np.logspace(np.log10(0.002), np.log10(2.0), 40)
    G, A = [], []
    total = 0.0
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            t, a, g = P.load_imu(s)
            dt = float(np.median(np.diff(t)))
            fs = 1.0/dt
            idx = P.still_windows(g)
            if idx is None or len(idx) == 0: continue
            idx = np.asarray(idx)
            # group the still samples into contiguous stretches
            brk = np.where(np.diff(idx) > 1)[0]
            for seg in np.split(idx, brk+1):
                if len(seg) < args.min_still*fs: continue
                total += len(seg)*dt
                for k in range(3):
                    G.append(allan(g[seg, k] - g[seg, k].mean(), dt, taus))
                    A.append(allan(a[seg, k] - a[seg, k].mean(), dt, taus))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)

    if not G:
        print("no still stretches long enough"); return
    Gm = np.nanmedian(np.array(G), axis=0)
    Am = np.nanmedian(np.array(A), axis=0)
    print(f"{len(G)//3} still stretches, {total:.0f} s of stillness in total\n")

    print(f"  {'tau (s)':>9}{'gyro dev (deg/s)':>20}{'accel dev (m/s2)':>20}")
    for i in range(0, len(taus), 4):
        if not np.isfinite(Gm[i]): continue
        print(f"  {taus[i]:>9.3f}{np.degrees(Gm[i]):>20.5f}{Am[i]:>20.6f}")

    Ng, Bg, Kg = fit_coeffs(taus, Gm)
    Na, Ba, Ka = fit_coeffs(taus, Am)
    print("\nGYROSCOPE")
    print(f"  N  white noise (ARW)     {np.degrees(Ng)*1000:8.2f} mdeg/s/sqrt(Hz)   "
          f"= {Ng:.3e} rad/s/sqrt(Hz)")
    print(f"  B  bias instability      {np.degrees(Bg)*3600:8.2f} deg/h")
    print(f"  K  rate random walk      {np.degrees(Kg)*1000:8.3f} mdeg/s/s/sqrt(Hz)  "
          f"= {Kg:.3e} rad/s/s/sqrt(Hz)")
    print("\nACCELEROMETER")
    print(f"  N  white noise (VRW)     {Na/9.80665*1e6:8.1f} ug/sqrt(Hz)")
    print(f"  B  bias instability      {Ba/9.80665*1e6:8.1f} ug")

    print("\nWHAT THE FILTER IS CURRENTLY USING, against what was just measured")
    cur_g, cur_b = np.deg2rad(0.05), np.deg2rad(0.002)
    print(f"  sigma_g   set to {cur_g:.3e}   measured N  {Ng:.3e}   "
          f"ratio {cur_g/Ng:6.1f}x too {'large' if cur_g > Ng else 'small'}")
    print(f"  sigma_b   set to {cur_b:.3e}   measured K  {Kg:.3e}   "
          f"ratio {cur_b/Kg:6.1f}x too {'large' if cur_b > Kg else 'small'}")
    print("\n  sigma_g enters Q as sigma_g^2 * dt, so it is a density in rad/s/sqrt(s) and")
    print("  is directly comparable with N. sigma_b likewise against K.")


if __name__ == "__main__":
    main()
