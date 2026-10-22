# Contributing

Please report installation/export errors with the bug template, and missed or incorrect
highlights with the event template. Include the release version, OS/Python, doctor output,
video resolution/frame rate, UI language/HUD scale, mode, timestamp and model/effort.
Remove personal paths and credentials before posting. A shareable crop or short sample is
more useful than an entire recording.

To validate a change:

```text
python apex.py setup
.\.venv\Scripts\python.exe scripts/test_pipeline.py
.\.venv\Scripts\python.exe scripts/test_public.py
```

On macOS/Linux, use `.venv/bin/python` for the last two commands. Alternatively activate
the project environment and run `python scripts/test_pipeline.py` and `python scripts/test_public.py`.
The tests need FFmpeg/ffprobe; real OCR checks need the OCR dependencies. Report skips explicitly.

Keep defaults at two seconds of tail and seven seconds of chaining. Alternative preferences
belong in validated configuration. Timing changes must propagate to planning, validation,
cache decisions and documentation. Avoid introducing audio-based gates or silently discarding
uncertain visual proposals.

For detection changes, compare against human-labelled real footage as described in
[validation](references/validation.md). Synthetic tests demonstrate pipeline behavior, not recall.
Do not change a compatibility entry to verified until that platform/model/workload was actually tested.
