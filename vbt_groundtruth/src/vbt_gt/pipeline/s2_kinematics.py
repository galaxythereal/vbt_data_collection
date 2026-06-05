"""S2 — RTS smoother & derivatives (M1).

Hand-rolled constant-jerk Kalman + RTS backward pass (NO library RTS). Produces
smoothed s, v, a, vertical velocity/accel, and state variances. Stub for Step-1.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics


def s2_kinematics(cond: Conditioned, params: Params) -> Kinematics:
    raise NotImplementedError("M1: hand-rolled constant-jerk RTS smoother — see docs/M1_conditioning_and_derivatives.md")
