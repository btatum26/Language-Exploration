# Language Exploration

This repository contains three active, independent Python projects:

- `alignment-workbench`: the application to launch for recording, multitrack editing, and PostgreSQL-backed alignment review. It owns the desktop window and its process-lifetime SSH tunnel, but never uses SQLAlchemy directly.
- `registry-aligner`: the Qt-free ingestion, forced-alignment, PostgreSQL, migration, export, and workbench-service backend. Pixi is the supported way to provision MFA; UV runs Python quality gates.
- `spectrogram-playground`: the reusable native audio, playback, analysis, and PyQtGraph package plus its standalone educational desktop application.

`voice-spectrogram-playground-DEPRICATED` is retained for history. It is not installed, imported, tested, or launched by the active applications.

## Launch the workbench

Use Python 3.11–3.13 and install each active project from its own directory:

```powershell
cd registry-aligner
pixi install
$env:UV_CACHE_DIR='.uv-cache'
uv sync

cd ..\alignment-workbench
$env:UV_CACHE_DIR='.uv-cache'
uv sync --extra dev
uv run alignment-workbench
```

Copy the database settings into the ignored `alignment-workbench/.env`. The established database is `registry_align`, reached through the workbench-owned `registry-db` SSH tunnel on local port `5433`:

```dotenv
REGISTRY_ALIGN_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:5433/registry_align
REGISTRY_ALIGN_DATABASE_ADMIN_URL=postgresql+psycopg://ADMIN:PASSWORD@127.0.0.1:5433/registry_align
```

Apply migrations before launching:

```powershell
cd registry-aligner
.\.pixi\envs\default\Scripts\registry-align.exe db upgrade --config .\registry-align.example.toml
```

## MFA discovery

The workbench uses this order: `REGISTRY_ALIGN_MFA` or an explicit configured executable, `PATH`, then Registry Aligner's Pixi environment (`Scripts/mfa.exe` on Windows or `bin/mfa` on Unix). The connection tooltip reports the selected executable or every searched location. Conda is not required.

## Tests

```powershell
cd registry-aligner
uv run pytest -m "not postgresql"
uv run ruff check .
uv run python -m compileall -q src

cd ..\spectrogram-playground
uv run pytest
uv run ruff check .
uv run python -m compileall -q spectrogram_playground

cd ..\alignment-workbench
uv run pytest
uv run ruff check .
uv run python -m compileall -q src
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database before running Registry Aligner's `postgresql` tests. Those tests drop/create tables and must never point at user data.
