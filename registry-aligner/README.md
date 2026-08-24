# Registry Aligner

`registry-aligner` is an independent Python CLI for ingesting recording registries, preparing
audio locally, and storing authoritative alignment metadata in PostgreSQL. It has no dependency
on the repository's spectrogram application or any Qt package.

## Architecture

```text
CLI -> AlignmentService
          |-- MetadataRepository / UnitOfWork -> PostgreSQL
          |-- AudioObjectStore -> remote content-addressed source audio over SSH/SCP
          |-- ArtifactStore -> disposable local MFA working files
          `-- AlignerBackend -> Montreal Forced Aligner
```

PostgreSQL owns logical recordings, immutable timestamped versions, alignment results, segments,
manual revisions, and compact run history. One exact source-audio object per SHA-256 is retained on
the SSH server. All versions with the same audio bytes reference the same `audio_assets` row and
remote object. Native-rate canonical PCM and 16 kHz mono alignment audio are disposable local
working files. SQLAlchemy objects and connections do not cross the repository boundary.

There is no SQLite backend, SQLite migration, or legacy run-directory import path.

## Setup

Python-only development:

```powershell
cd registry-aligner
uv sync --extra dev
Copy-Item .env.example .env
Copy-Item registry-align.example.toml registry-align.toml
```

Put the actual passwords into `REGISTRY_ALIGN_DATABASE_URL` and
`REGISTRY_ALIGN_DATABASE_ADMIN_URL` in `.env`. Normal operations exclusively use the application
URL. The admin URL is used only by explicit `db create-dev`, `db upgrade`, and `db reset-test`
commands. TOML never contains credentials. `.env` is ignored by Git.

For MFA and FFmpeg on Windows, Pixi supplies the native dependencies:

```powershell
pixi install
pixi run models
```

## SSH tunnel and database

The example configuration inherits the working OpenSSH setup directly:

```text
ssh -N registry-db
```

Normal CLI commands start that tunnel without a window, wait for `127.0.0.1:5433`, use the
PostgreSQL connection, and terminate the tunnel. Port 5433 must be free before a command starts.
The exact connectivity check is:

```powershell
uv run registry-align db check --config registry-align.toml
```

Source audio uses the same `registry-db` alias through `ssh` and `scp`. The durable remote root is:

```text
~/registry-align/audio/
```

Each unique source SHA-256 has exactly one extension-independent object key:

```text
~/registry-align/audio/<sha-prefix>/<source-sha256>
```

Create a development database (the URL database name must end in `_dev`) and migrate it:

```powershell
uv run registry-align db create-dev --config registry-align.toml
uv run registry-align db upgrade --config registry-align.toml
uv run registry-align db check --config registry-align.toml
```

The application refuses to process against a missing or old schema. It never drops or recreates
normal databases. A disposable test database can be reset only when its name ends in `_test`:

```powershell
$env:REGISTRY_ALIGN_DATABASE_URL = $env:TEST_DATABASE_URL
uv run registry-align db reset-test --confirm --config registry-align.toml
```

## Registry format

Canonical input uses stable registry IDs and relative audio paths:

```json
{
  "schema_version": "1.0",
  "defaults": {"language": "it"},
  "entries": [
    {
      "id": "it_basics_001_luca",
      "audio_path": "audio/luca/001.wav",
      "transcript": "lidi, visti, fini",
      "speaker_id": "luca",
      "metadata": {"lesson_id": "it_basics_001"}
    }
  ]
}
```

`[input]` accepts dotted property paths for other JSON layouts. Paths are logical and relative;
absolute machine paths are never authoritative metadata.

## Ingestion and versioning

Default processing trusts PostgreSQL. If a registry ID exists, it is reported as `skipped` before
the incoming audio is hashed or compared:

```powershell
uv run registry-align process registry.json --config registry-align.toml
```

When every selected ID already exists, the entire skip run is written in one transaction and MFA,
FFmpeg, source hashing, and remote audio checks are not started.

Explicit overwrite means append-if-changed, never destructive replacement:

```powershell
uv run registry-align process registry.json --overwrite-existing --config registry-align.toml
```

The complete content fingerprint covers source-byte SHA-256, exact transcript hash, language,
speaker, and canonicalized user metadata. Identical content is `unchanged`. Changed content creates
a new opaque UUID version and updates the recording's current pointer. History is ordered by UTC
`created_at`; versions are never displayed as `v1`, `v2`, and so on. Identical audio bytes and
identical transcript content are reused independently across logical recordings.

Alignment identity additionally covers backend/model/dictionary, normalizer/tokenizer, audio
preparation, and pipeline identity. A uniqueness constraint prevents duplicate processing results.
A committed lease records in-flight work so MFA runs with no open database transaction and stale
work can be reclaimed.

## Audio storage and local working files

The original source bytes are uploaded atomically and verified by SHA-256 before an `audio_assets`
row is committed. Existing verified objects are reused. Transcript, metadata, speaker, or language
changes therefore create recording versions that keep pointing to the same audio asset.

New objects are checked and transferred in batches instead of opening SSH/SCP for every file.
Upload workers run in the background while FFmpeg prepares later recordings; PostgreSQL ingestion
still waits for the corresponding batch to pass remote hash verification. Tune the bounded
concurrency in `[remote_audio]`:

```toml
batch_size = 24
upload_workers = 2
```

Fetch the current audio for a recording from any configured client:

```powershell
uv run registry-align audio fetch it_basics_001_luca --output .\it_basics_001_luca.mp3 --config registry-align.toml
```

The download is written atomically and rejected if its bytes do not match PostgreSQL metadata.
FFmpeg/MFA files exist only inside `.registry-align-cache/work/<run-id>` while a run is active and
are removed when the run finishes. They are never uploaded to the server.

```powershell
uv run registry-align cache status --config registry-align.toml
```

## Operations

```powershell
uv run registry-align doctor --registry registry.json --config registry-align.toml
uv run registry-align validate registry.json --config registry-align.toml
uv run registry-align plan registry.json --config registry-align.toml
uv run registry-align status --config registry-align.toml
uv run registry-align history it_basics_001_luca --config registry-align.toml
uv run registry-align qc --config registry-align.toml
uv run registry-align export --format jsonl --format textgrid --config registry-align.toml
uv run registry-align maintenance prune-runs --dry-run --config registry-align.toml
```

Run pruning deletes only expired run items/issues and their run rows. It keeps active runs, at least
the configured number of recent runs, and all recordings, versions, assets, transcripts, alignment
results, segments, and accepted revisions.

## Database tables

| Table | Purpose |
|---|---|
| `recordings` | Stable registry identity and explicit current-version pointer |
| `audio_assets` | Deduplicated source-byte identity, remote object key, and audio metadata |
| `transcript_versions` | Exact/normalized text, hashes, tokens, mappings, warnings |
| `recording_versions` | Immutable content snapshots, supersession, current alignment pointer |
| `alignment_results` | Processing identity, lease/status, provenance, QC summary |
| `segments` | Immutable word/phone/silence/utterance model output |
| `segment_revisions` | Immutable manual edits layered over model segments |
| `runs` | Compact operational invocation history |
| `run_items` | Per-input run outcome and durable-object references |
| `issues` | Retainable run-scoped diagnostic events |

## Development and tests

Unit/static checks do not need a database. PostgreSQL integration tests use `TEST_DATABASE_URL`
when supplied. Otherwise, when both configured database URLs are available, they create and drop a
unique temporary schema through the admin role without touching application tables. SQLite is never
substituted.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://registry_align_app:PASSWORD@127.0.0.1:5433/registry_align_test"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest tests/unit tests/integration/test_cli.py
uv run pytest tests/integration/test_postgresql.py
uv build
```

If the tunnel fails, first run `ssh -N registry-db` directly to inspect SSH configuration, stop it,
and retry the CLI so port 5433 is available. Connection failures redact URL credentials. If the
schema revision is behind, run `registry-align db upgrade`; normal processing will not migrate it
silently.
