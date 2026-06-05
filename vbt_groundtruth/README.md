# vbt_gt — offline camera-only VBT ground-truth pipeline

Turns a RealSense single-marker 3D barbell trajectory (90 fps) into frame-accurate
rep boundaries, phase labels, per-rep VBT kinematics, and a calibrated confidence.
**Camera data only — the IMU is out of scope.**

**This is the Step-1 scaffold** (per `../docs/AGENT_RUNBOOK.md`): frozen contracts +
`NotImplementedError` stubs only, no algorithms yet. Built milestone-by-milestone M0→M5.

## Layout (FOUNDATION §0.3)
- `src/vbt_gt/types.py` — dataclasses + enums (frozen contract, §0.5)
- `src/vbt_gt/config.py` — `Params`, `EXERCISE_CONFIG` (§0.6/§0.7)
- `src/vbt_gt/io/` — `canonical` (RawSession persistence), `adapter` (real format → RawSession, M0),
  `synth` (synthetic generator, M0), `writers` (parquet, M5)
- `src/vbt_gt/pipeline/` — stages `s0_sets` … `s8_kinematics_vbt`
- `src/vbt_gt/metrics/eval.py` — acceptance-metrics harness (M0)
- `src/vbt_gt/run.py` — orchestrator (§0.9)
- `tests/` — pytest

## Authoritative specs
The frozen specs live in the repo-root `../docs/` (`00_FOUNDATION.md`, `M0`…`M3-M5`,
`AGENT_RUNBOOK.md`, `REPO_MAP.md`, `camera_config.md`). Those win on any conflict.

## Dev setup
```sh
python -m pip install -e ".[dev]"      # from this directory
python -c "import vbt_gt.types, vbt_gt.config"
pytest
```
