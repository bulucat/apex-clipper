# 2.7.0

- Fix HUD corrections retaining omitted old counters/view labels and incorrect pre-scan status reporting.
- Add guarded run/next/submit task cards, bounded visual work, program-owned evidence, detail requests and resumable pauses.
- Add a Windows launcher, explicit unreviewed marked-excerpt export, and optional OBS success feedback/status.
- Preserve strict minute/final 150 damage gates and all media/delivery guards.
- Separate engineering controller validation from unmeasured real-game model accuracy.

# 2.6.8 — 2026-10-22

- Fix explicit full-source review: canonical match registration and admission checks no longer require a minute-HUD state for a full_scan. Final per-segment damage review remains mandatory.
- Permit the actual first source frame as both before/start proof for cuts beginning at zero, requiring consistent damage readings; later cuts still require a strictly earlier frame.
- Refresh generated-media and unit-test fixtures to import bound HUD observations, canonical matches, exact-cut damage proof and current job/config identities through the real validation APIs.
- Align old default-profile assertions with the current 2fps HUD gate and compatible speed alias.
- Run all regression tests in the platform/Python CI matrix. Restrict push triggers to main and cancel superseded runs to avoid duplicate branch/tag notifications.

# 2.6.7 — 2026-10-22

- Add the OBS Lua highlight marker: configurable hotkey, 60-second lookback, sidecar JSON and recording lifecycle handling.
- Restrict the first HUD scheduling stage to merged marked ranges on the original timeline. Preserve existing event, 150-damage, ammo, cut and delivery checks.
- Keep explicit marker selection and ignore choice across same-job resumes, including ROI config changes and temporarily unavailable sidecars.
- Retry failed saves after recording stops; preserve pending sessions and protect existing, corrupted, backup and pending marker files.
- Reject invalid or empty marker input without silently scanning the entire video.
- Include the OBS Lua script in official source ZIPs; add marker, persistence and packaging regression tests and a dedicated CI workflow.
- Windows OBS/Lua and engineering media checks were run locally. Real-game hotkeys, long-recording alignment and actual cost savings remain unmeasured.

# 2.6.1-beta.1 — 2026-10-18

- Preserve partial HUD counters and reject proven negative minutes without unnecessary recovery; unknown is never zero.
- Generate compact worker tasks and validate source-bound, disjoint submissions with hud-import.
- Require explicit top-center spectator evidence on worker imports; document downed shield and weapon-switch failures.
- Select tall magazine digits instead of wide reserve digits; uncertain magazines remain null, numeric decreases never directly set fire_start_hint.
- Reuse initial source hash for local review/ammo only while source stat matches; final export still verifies source bytes.
- Add Luna execution rules and focused regression coverage; real-video end-to-end speed and recall remain unmeasured.

# 2.6.0-beta.1 — 2026-10-18

- Default minute HUD gate: kill/assist growth and >=150 damage, single midpoint refinement.
- Missing HUD: nine fixed ten-way recovery samples; no recursive resampling, same-match comparisons only.
- Local dense scan coverage/provenance and review validation replace mandatory full-video coverage in default mode.
- Own-view assist-elimination banners are accepted and use the same 2s/7s clipping and gunfire recovery.
- Full 4fps and legacy economy 2fps remain explicit profiles; actual recall/cost savings remain unmeasured.

# Changes

## 2.5.0-beta.1

- Added a single setup/doctor/calibrate/scan/review/plan/export entry point.
- Added project-local dependency installation, validated shared configuration and ROI previews.
- Preserved the author's default two-second tail and inclusive seven-second chain policy.
- Added compact phase/progress reporting and review-aware status/resume support.
- Added Chinese/English user documentation, issue forms, cross-platform CI definitions and clean source packaging.
- Kept model/gameplay recall and non-Windows compatibility explicitly unverified pending real tests.

## 2.4.0

- Added 2fps economy scanning, local OCR hints, exact glyph-mask deduplication and review caches.
- Added two-second tails and chaining for adjacent confirmed events within seven seconds.
- Retained full-sample review mode, own-view boundaries and forward-only ammo start extension.
