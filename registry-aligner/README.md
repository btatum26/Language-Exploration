# Registry Aligner

`registry-aligner` is an independent command-line subproject for converting JSON recording
registries into canonical, backend-independent alignment jobs. It does not import or depend on
the repository's spectrogram application or Qt.

The implementation covers registry ingestion and the complete processing pipeline:

- strict TOML configuration models;
- canonical and dotted-path mapped JSON registries;
- exact raw transcript preservation;
- stable UUIDv5 identifiers for mapped entries without IDs;
- relative-path, traversal, absolute-path, and symlink-containment checks;
- duplicate ID and normalized-path checks;
- aggregated human-readable or JSON validation issues;
- side-effect-free planning with selection support;
- FFprobe metadata and source hashing;
- native-rate canonical PCM plus 16 kHz mono alignment derivatives;
- raw-preserving transcript normalization and token mappings;
- transactional SQLite migrations and canonical records;
- version-aware MFA legacy and hosted-model subprocess commands;
- canonical word, phone, silence, and utterance segments;
- structural QC, JSONL/CSV/TextGrid exports, manifests, and verified resume caching;
- `init`, `doctor`, `validate`, `plan`, `prepare`, `process`, `status`, `qc`, and `export` commands.

## Setup with Pixi

```powershell
cd registry-aligner
pixi install
pixi run registry-align --help
```

Pixi installs Python, FFmpeg/FFprobe, Montreal Forced Aligner, Kaldi, Epitran, and the Python package into
one project-local environment. It uses the locked conda-forge packages directly without requiring
Conda, Miniconda, or environment activation. MFA's model store is kept locally in the ignored
`.mfa` directory. The model task downloads the Italian CV acoustic model and base dictionary,
then derives a registry-specific dictionary containing any missing Italian words:

```powershell
pixi run models
pixi run doctor
```

The included MFA profile uses the wider search beam required by the two fastest number-list
recordings in this registry.

The project also retains its `uv` metadata for Python-only development, but `uv` alone cannot
provide MFA's native Kaldi runtime on Windows. Use Pixi for actual alignment.

## Canonical registry

```json
{
  "schema_version": "1.0",
  "defaults": {"language": "it"},
  "entries": [
    {
      "id": "it_basics_001_luca",
      "audio_path": "audio/luca/001.wav",
      "transcript": "lidi, visti, finí",
      "speaker_id": "luca",
      "metadata": {"lesson_id": "it_basics_001"}
    }
  ]
}
```

Audio paths are resolved from the registry file's parent directory. Source JSON and audio are
never modified.

## Mapped registry

Copy `registry-align.example.toml` and edit its `[input]` dotted paths. A root JSON array uses
`entries_path = "$"`. Property paths only traverse JSON objects; expressions and array indexing
are not accepted.

```powershell
pixi run registry-align validate path\to\registry.json --config path\to\registry-align.toml
pixi run registry-align plan path\to\registry.json --config path\to\registry-align.toml
pixi run registry-align plan path\to\registry.json --config path\to\registry-align.toml --json
pixi run registry-align process path\to\registry.json `
  --config path\to\registry-align.toml `
  --output path\to\alignment-output
```

Supplying a mapping configuration enables stable ID derivation for entries whose mapped ID is
missing. Canonical input without a mapping requires an explicit ID.

`process --json` emits newline-delimited progress events followed by a final structured result.
It never invokes a shell command string and never downloads MFA models. For the included Italian
registry, the complete workflow is exposed as Pixi tasks:

```powershell
pixi run models
pixi run doctor
pixi run validate
pixi run pilot
pixi run process
pixi run status
pixi run qc
pixi run export
```

## Initialize templates

```powershell
pixi run registry-align init path\to\registry-directory
```

This writes `registry-align.toml` and `canonical-registry.schema.json`. Existing files are not
overwritten unless `--force` is supplied.

## Quality gates

```powershell
pixi run quality
```
