# Attribution and scope

The skill's original metadata identifies `Apex-Shorts-Automator-main` as its source project.
That attribution is retained in `SKILL.md`. Keep any applicable upstream notices when
incorporating additional source code; this package does not bundle the retired vertical-video workflow.

The MIT license covers this project's code and documentation. Reference crops in
`assets/reference/` contain gameplay UI and player text; the code license does not
relicense third-party game artwork or trademarks. The project is not affiliated with
or endorsed by Apex Legends, EA, or Respawn.

FFmpeg, RapidOCR, ONNX Runtime, NumPy and Pillow are external dependencies with
their own licenses. Their binaries and model weights are not included in the source ZIP.
RapidOCR's installed package supplies its OCR models. Installation downloads dependencies;
runtime OCR does not call a cloud OCR API.

The assist-elimination gameplay reference was supplied by the user for banner recognition.
It demonstrates the visual phrase, not a verified full-video detection benchmark.
