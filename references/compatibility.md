# Compatibility and evidence status

## OBS manual markers (2.6.7)

The Lua script was exercised with OBS 32.2.2 LuaJIT on Windows, including real SWIG JSON/file I/O and Chinese paths.
Frontend events and frame counts in these tests are simulated. Marker-scoped HUD scheduling, resume selection and
delivery validation use engineering video fixtures, not real Apex judgments.

Real full-screen hotkeys and long gameplay recording alignment have not been measured. Automatic file splitting,
source moves and remuxed files are not supported. Native OBS tests require Windows and OBS_BIN_DIR;
a skipped native test is not a platform pass. The dedicated marker CI workflow requires LuaJIT for its lifecycle tests.

| Item | Status |
|---|---|
| Windows + Python3.12 + FFmpeg/ffprobe9.0.2 | Pipeline, OCR and synthetic-media checks run locally |
| Python3.11 | 2.6.8 engineering CI passed; latest changes follow repository Actions |
| Linux/macOS | 2.6.8 engineering CI passed; no native gameplay/hotkey claim |
| RapidOCR ONNXRuntime1.4.4 | Local reference-crop and numeric OCR exercised; Chinese words can be misread |
| Other AI hosts | Must supply local image viewing, script execution and file access; not separately verified |
| Luna medium / Sol medium / other models | No real-game comparative benchmark yet |
| Other UI languages, game modes, HUD scale, ultrawide/VFR recordings | Calibrate and test before treating as supported |
| Real-game recall, false positives, total runtime and model cost | Not measured on labelled recordings |

The current default profile preserves the author's banner criteria and screen regions.
Terms such as Knockdown/Damage in instructions are vocabulary, not proof that an English
layout/model combination was tested. New profiles need example evidence and labelled tests.

The application retains original frame rate and dimensions. Mixed-source compilations and
variable-frame-rate inputs need separate verification; a successful environment check does not
certify every possible source format.

The script can check local dependencies. It cannot prove that your model receives image pixels
or that it actually inspected the evidence. No verified model-performance badge is supplied.

The compatibility matrix is intentionally conservative. Update it from actual local/CI runs,
and separate a skipped test from a passing test.

## V2.6 minute-gate update

The minute/single-midpoint/ten-way scheduling and assist-elimination export have synthetic-media
behavioral tests. HUD numbers in those tests are injected fixtures, not real-video recognition.
The supplied gameplay reference confirms the default crop includes all four counters and the
assist banner. Real-game recall and runtime/token savings for this new gate remain unmeasured.
