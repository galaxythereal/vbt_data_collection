# datasets/

> **WHAT IS IN THE REPOSITORY.** The raw measurement is **not** published: `camera/`
> (marker positions, infrared video) and `imu/` (the inertial streams) are about 7.6 GB and
> are held outside the repository. Everything derived from them is here — the annotation
> (`annotation_live.csv`, `annotation_online.csv`, `annotation_offline.csv`,
> `annotation_reviewed.csv`), `ground_truth.csv`, `metadata.json`, `rotation.json`,
> `sync_map.csv`, `manifest.json`, `events.jsonl`, `CHECKSUMS.sha256`.
>
> Two files are excluded on size rather than on principle: `smoothed.csv`, the per-frame
> reference track (118 MB over the corpus), and the per-session audit images (165 MB).
> **Two sample sessions carry both** so the pipeline can be run end to end from a clone:
> `session_20260510_121411` (biceps curl, 8 repetitions) and `session_20260520_142117`
> (bench press, 27). Re-deriving the annotation for the other 82 needs the raw data.
>
> Subject identifiers are pseudonyms (`S01`…). All participants consented to the use of the
> recorded data. The layout below describes a session as recorded, including the parts held
> back, so that the schema is complete.


One folder per session. Nothing is nested by processing stage, and no folder is named
after a process.

    session_YYYYMMDD_HHMMSS/
      metadata.json  manifest.json  events.jsonl  CHECKSUMS.sha256
      camera/                  READ-ONLY   ir_video.mp4, marker_positions.csv,
                                           depth_at_marker.csv, video_frames.csv
      imu/                     READ-ONLY   raw_imu.bin, raw_imu.csv, camera_imu.csv
      rotation.json                        the camera's own tilt; direction only is used
      smoothed.csv                         the whole-session smoothed track, with the
                                           uncertainty of every value
      sync_map.csv                         which inertial sample is which camera frame,
                                           and the frame rate measured from the trigger
      annotation_live.csv                  what the annotator produced DURING the set
      annotation_online.csv                the same causal annotator, re-run on the
                                           corrected track
      annotation_offline.csv               the post-session annotation, exactly as the
                                           algorithm produced it
      annotation_reviewed.csv              a person's accept/reject over it
      audit_post_session.png               the audit: height, speed and push in one image,
                                           drawn at a fixed size from the data alone
      audit_imu.png                        the camera against the inertial estimate:
                                           height, velocity, and each filter's error

    ground_truth.csv           the released label set: 1400 accepted repetitions,
                               46 columns
    REVIEW.md                  who reviewed the annotation and what they could change
    KNOWN_LIMITATIONS.md       everything known to be wrong or disputed

## ground_truth.csv

Self-contained by design: nothing downstream should have to open a session directory to
understand a rep. A rep a person refused is not in this file at all -- the refusal itself
is recorded in that session's annotation_reviewed.csv.

  identity     session_id, session_date, subject_id, exercise, down_first, load_kg,
               target_reps (what was PRESCRIBED -- never a count of what happened),
               reviewed (a person has judged this session),
               rep_id (1..N over the RELEASED repetitions, no gaps),
               annotation_rep_id (the number it has in annotation_offline.csv, so a row
               can be traced back to the annotation and the audit)
  frames       rep_start_frame, turnaround_frame, rep_end_frame,
               concentric_start/end_frame, eccentric_start/end_frame
               (the same index the video is seeked by: frame N is video frame N)
  seconds      rep_start_s, rep_end_s, duration_s, concentric_s, eccentric_s
  position     height_start_m, height_turn_m, height_end_m, rom_m,
               return_error_m (end minus start: how well the round trip closed)
  velocity     con_mean_velocity_ms, con_peak_velocity_ms, con_time_to_peak_s,
               ecc_mean_velocity_ms, ecc_peak_velocity_ms
  push         con_peak_accel_ms2, ecc_peak_accel_ms2, peak_jerk_ms3
  quality      gap_frames, measured_fraction, pos_sd_median_m, vel_sd_median_ms,
               started_below_band, started_above_band
  context      line_low_m, line_mid_m, line_high_m, camera_tilt_deg,
               smoother_nis_vertical

Seconds are computed from the frame rate measured for that session from the camera's
own trigger pulses -- 89.8654 Hz, not 90.000. See sync_map.csv.

`camera/` and `imu/` are the measurement: read-only, checksummed, never written by
anything downstream. Everything else in the folder is derived from them and can be
deleted and rebuilt. The session folder itself is writable so the derived files can sit
beside the measurement.

`annotation_offline.csv` is never edited by the review window. A rep the reviewer refused
is recorded as a refusal in `annotation_reviewed.csv`, so the record shows what was
refused rather than quietly missing it.

## The inertial audit

`audit_imu.png` in every session compares the camera with the inertial estimate. Three
panels: height, velocity, and each attitude filter's height error against the camera.

  camera        white, the reference, running the whole session
  VQF           blue, solid
  ESKF          orange, dashed on top -- the two agree closely enough that drawing one
                over the other simply hides it

The inertial traces appear as one segment per repetition, because that is where the
round-trip boundary conditions apply, and each starts from the camera's height at that
repetition: absolute height is not observable from an inertial sensor, and starting each
segment anywhere else would hide the drift the audit exists to show.

Over 84 sessions, the median error inside a repetition:

|  | height | velocity |
|---|---|---|
| VQF | 18.8 mm (90th 45.1) | 39.9 mm/s (90th 59.3) |
| ESKF | 19.4 mm (90th 52.6) | 44.4 mm/s (90th 70.4) |

    .venv/bin/python scripts/imu/audit_imu.py --n 84

## Rebuilding everything derived

    ./build/offline_pass --audit --export               # every session
    ./build/offline_pass datasets/session_20260510_124504

      --audit    also write audit_post_session.png
      --export   also update ground_truth.csv, honouring annotation_reviewed.csv

That is the same code the app's "Post-session annotation" button runs
(`src/offline/OfflinePipeline`), not a second implementation of it.

## Checking the app against an independent implementation

`scripts/reference/annotate_v2.py` implements the same rules separately, and is kept so
the C++ can be checked against something not derived from it.

    ./build/offline_pass --quiet
    python3 scripts/reference/annotate_v2.py
    python3 scripts/reference/crosscheck.py

Last run: **1409/1409 identical.** That is the annotator's output before review.
The released set is **1400** repetitions: see [REVIEW.md](REVIEW.md).
