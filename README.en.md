# Apex Clipper

Provide the recording and ask to clip an Apex video. Use the current installed skill without naming a version. New/re-cut requests use fresh workdirs; only same-job resumes reuse compatible caches.

[中文说明](README.md) · **2.7.0**

An AI-assisted Apex Legends highlight workflow. Local visual proposals and CPU OCR
identify material for review; an image-capable assistant confirms evidence and invokes FFmpeg.
Output keeps the source dimensions, frame rate and existing audio.

Defaults: end two seconds after the last confirmed knockdown/assist-elimination/reward banner; chain adjacent
confirmed events no more than seven seconds apart. Recover earlier gunfire from the ammo display.
No audio-peak or Top K candidate selection.

This beta requires visual review. OCR output alone does not confirm a kill, and the project
has no measured real-game recall, model comparison or token-savings benchmark yet.

## Guided entry (2.7)

On Windows, open **Start-Apex-Clipper.cmd** after extracting the whole project. Python and FFmpeg are still required. The local launcher checks dependencies and shows the current visual task. No embedded model account or API key is supplied.

Use python apex.py run --source recording.mp4, then follow next/submit. The program controls transitions and binds evidence; the assistant answers task cards. Unknown decisions pause safely. Strict clipping keeps all existing rules. The separate marked-export option exports marked excerpts without claiming event/damage review. Real-game Luna accuracy is not established.

See [guided review](references/guided-review.md).

## Mark highlights while recording in OBS

Load [obs/apex-marker.lua](obs/apex-marker.lua) through OBS **Tools → Scripts**, then assign
**Apex：标记精彩操作** a hotkey such as F8 under **Settings → Hotkeys**.
Press it after a highlight and stop recording normally. The script saves a companion
`.apex-markers.json` beside the recording; it does not modify the video.

The clipper reviews only the merged windows preceding each mark (60 seconds by default).
All existing HUD, event, ammo and final 150-damage checks remain in force. Unmarked footage is outside
the review scope. Empty, incomplete or invalid marker files fail explicitly instead of triggering a full scan.
Use single-file recording with automatic splitting disabled, keep the original path/name,
and verify one short recording before relying on the hotkey. See [marker workflow](references/obs-markers.md).

## Requirements

- Python; 3.11/3.12 recommended.
- FFmpeg and ffprobe on PATH, or configured local executable paths.
- An AI host that can execute scripts, access recordings and pass local images to a vision model.
- Disk space for local evidence and exports. Initial dependency installation requires internet.

CPU OCR does not call a cloud OCR API. Images reviewed by your assistant are processed
according to the AI service you choose; this is not a claim of fully offline operation.

## Quick start

From the extracted project directory:

```text
python apex.py setup
python apex.py doctor
python apex.py calibrate --source "recording.mp4" --at 60 --outdir exports/calibration
python apex.py scan --source "recording.mp4" --workdir exports/full/run1 --config exports/calibration/config.json
python apex.py status --workdir exports/full/run1
```

Setup creates a project-local `.venv`; future commands use it automatically. System Python is unchanged.
If FFmpeg is not on PATH, use `python apex.py init --ffmpeg "actual/path/ffmpeg" --ffprobe "actual/path/ffprobe"`.
Windows executable paths usually end in `.exe`.

Check the calibration overlays: LOW must contain both banner lines, HIGH both spectator rows,
AMMO the large magazine digits, and STATS all four top-right counters. Ask your assistant to adjust the saved JSON ROI if needed.
Calibration generates previews and settings; it does not certify that the rectangles are correct.

Install the complete folder into your AI host's supported skill location. Follow that host's
installation instructions and give it `SKILL.md` plus the recording path. Hosts and models
without image input cannot complete this workflow's visual-review step.

Default admission uses actual minute HUD readings: kill OR assist growth >=1 AND damage growth >=150.
Bisect admitted minutes once, then scan only selected windows at 2fps. Use a canonical match registry
and fixed ten-way recovery for unknown HUD. Never interpolate readings. See [delivery guards](references/quality-guards.md).

The assistant reviews selected local pages, records real decisions, fills confirmed events, plans clips
and checks final intervals. It then runs:

```text
python apex.py audit --workdir exports/full/run1
python apex.py ammo --workdir exports/full/run1 --all
# Visually review and import ammo decisions before planning.
python apex.py plan --workdir exports/full/run1
# Review complete intervals and import start-bound reviews before exporting.
python apex.py export --workdir exports/full/run1 --validate-only
python apex.py export --workdir exports/full/run1
```

Rerun the same scan command to resume an interruption. Status reports unread pages and the current
phase. Unreviewed material is not silently treated as negative. The command sequence does not
provide an automatic cloud model client or bypass the assistant's review responsibility.

## Configuration and modes

`python apex.py init --output my-config.json` creates an editable profile. Set timing, ROI,
sampling, countback and review density there. Pass `--config` when scanning; the job remembers
the file for subsequent commands. Changed scan/ROI settings require rescanning and review.
Changed timing requires replanning and final interval review. Cache keys include relevant settings.

Default (speed is a compatible alias): one HUD image per minute; kill OR assist count growth plus >=150 damage growth.
Bisect a qualifying minute once, applying only count growth to the halves. Missing HUD triggers
nine fixed ten-way interior images, without recursion. Apply the damage gate once to the same-match
minute/run, not each six-second interval. Only selected windows receive 2fps local banner scans.
An own-view assist-elimination banner is accepted without +100 or a second damage line.
Legacy whole-video 2fps remains explicit via profiles/legacy-economy.json.
Full-source sampling is an explicit user-selected alternative. Conflicting match labels, unbound/inferred HUD readings, events outside admitted windows, missing ammo reviews and mid-fire clip starts block delivery.
Short, obscured and blurry banners may be missed in either mode. Exact seven-second boundaries
require denser local review. Tail length must not exceed chain gap so separate clips cannot cover the next anchor.

The existing banner vocabulary/profile is primarily based on the author's Chinese UI examples.
English terms in the rules do not establish tested English-UI compatibility. Calibrate and validate
new languages, HUD layouts and game modes before relying on them.

Windows is the currently verified platform. Linux/macOS CI jobs are supplied, not claimed as
already passing. See [compatibility](references/compatibility.md), [workflow commands](references/public-workflow.md),
[contributing](CONTRIBUTING.md) and [validation](references/validation.md).

Code/docs: [MIT](LICENSE). Dependencies and reference image scope: [NOTICE](NOTICE.md).
`python apex.py package` creates a clean source ZIP; it does not publish to GitHub.
