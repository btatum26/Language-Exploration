# Repository guidance

- `src/model`: authoritative application state; `src/audio`: decoding, mixing, playback, recording; `src/analysis`: cached numerical work; `src/ui`: main-thread Qt widgets; `tests`: unit and headless Qt coverage.
- Run: `uv run python app.py`. Test: `uv run pytest`. Lint: `uv run ruff check .`. Package: `uv run pyinstaller SpectrogramPlayground.spec --clean --noconfirm`.
- The output callback may only mix prepared arrays, apply gain/mute/solo, handle loop boundaries, fill output, and advance the shared frame position. Never decode, analyze, perform I/O, log continuously, allocate large arrays, or touch Qt there.
- Only the Qt main thread may mutate widgets. Worker results must cross a Qt signal before display.
- A change is done when focused tests and the full suite pass, callback and UI thread rules remain intact, streams close cleanly, and affected user documentation is current.
