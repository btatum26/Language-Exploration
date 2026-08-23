"""Explicit SQLite schema migrations."""

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            registry_path TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            backend_name TEXT,
            backend_version TEXT,
            model_id TEXT,
            counts_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE recordings (
            recording_id TEXT PRIMARY KEY,
            source_registry_path TEXT NOT NULL,
            source_entry_index INTEGER NOT NULL,
            audio_relative_path TEXT NOT NULL,
            audio_resolved_path TEXT NOT NULL,
            transcript_raw TEXT NOT NULL,
            language TEXT NOT NULL,
            speaker_id TEXT,
            metadata_json TEXT NOT NULL,
            id_was_derived INTEGER NOT NULL,
            source_audio_sha256 TEXT,
            source_codec TEXT,
            source_sample_rate_hz INTEGER,
            source_channels INTEGER,
            source_duration_s REAL,
            canonical_pcm_path TEXT,
            canonical_pcm_sha256 TEXT,
            canonical_sample_rate_hz INTEGER,
            canonical_channels INTEGER,
            canonical_frame_count INTEGER,
            alignment_audio_path TEXT,
            alignment_audio_sha256 TEXT,
            alignment_sample_rate_hz INTEGER,
            latest_run_id TEXT,
            FOREIGN KEY(latest_run_id) REFERENCES runs(run_id)
        );
        CREATE UNIQUE INDEX ix_recordings_source
            ON recordings(source_registry_path, source_entry_index);
        CREATE INDEX ix_recordings_source_hash ON recordings(source_audio_sha256);
        CREATE TABLE transcripts (
            run_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            normalized_sha256 TEXT NOT NULL,
            normalizer_name TEXT NOT NULL,
            normalizer_version TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            PRIMARY KEY(run_id, recording_id),
            FOREIGN KEY(run_id) REFERENCES runs(run_id),
            FOREIGN KEY(recording_id) REFERENCES recordings(recording_id)
        );
        CREATE TABLE source_tokens (
            run_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            token_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY(run_id, token_id),
            FOREIGN KEY(run_id, recording_id) REFERENCES transcripts(run_id, recording_id)
        );
        CREATE TABLE alignment_tokens (
            run_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            token_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY(run_id, token_id),
            FOREIGN KEY(run_id, recording_id) REFERENCES transcripts(run_id, recording_id)
        );
        CREATE TABLE token_mappings (
            mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            source_token_ids_json TEXT NOT NULL,
            alignment_token_ids_json TEXT NOT NULL,
            relationship TEXT NOT NULL,
            FOREIGN KEY(run_id, recording_id) REFERENCES transcripts(run_id, recording_id)
        );
        CREATE TABLE segments (
            segment_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            tier TEXT NOT NULL,
            label TEXT NOT NULL,
            backend_label TEXT NOT NULL,
            start_s REAL NOT NULL,
            end_s REAL NOT NULL,
            start_sample INTEGER NOT NULL,
            end_sample INTEGER NOT NULL,
            timebase_sample_rate_hz INTEGER NOT NULL,
            parent_segment_id TEXT,
            source_token_ids_json TEXT NOT NULL,
            confidence REAL,
            review_status TEXT NOT NULL,
            source TEXT NOT NULL,
            model_id TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(run_id),
            FOREIGN KEY(recording_id) REFERENCES recordings(recording_id),
            FOREIGN KEY(parent_segment_id) REFERENCES segments(segment_id)
        );
        CREATE INDEX ix_segments_recording_tier_start
            ON segments(recording_id, tier, start_sample);
        CREATE INDEX ix_segments_label_tier ON segments(label, tier);
        CREATE INDEX ix_segments_parent ON segments(parent_segment_id);
        CREATE TABLE segment_revisions (
            revision_id TEXT PRIMARY KEY,
            segment_id TEXT NOT NULL,
            base_run_id TEXT NOT NULL,
            start_sample_override INTEGER,
            end_sample_override INTEGER,
            label_override TEXT,
            review_status TEXT NOT NULL,
            author TEXT,
            created_at TEXT NOT NULL,
            reason TEXT,
            FOREIGN KEY(segment_id) REFERENCES segments(segment_id),
            FOREIGN KEY(base_run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            recording_id TEXT,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES runs(run_id),
            FOREIGN KEY(recording_id) REFERENCES recordings(recording_id)
        );
        CREATE TABLE issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            recording_id TEXT,
            code TEXT NOT NULL,
            severity TEXT NOT NULL,
            stage TEXT NOT NULL,
            message TEXT NOT NULL,
            hint TEXT,
            details_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id),
            FOREIGN KEY(recording_id) REFERENCES recordings(recording_id)
        );
        CREATE INDEX ix_issues_run_severity_recording
            ON issues(run_id, severity, recording_id);
        CREATE TABLE cache_entries (
            cache_key TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL,
            result_run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            canonical_pcm_path TEXT NOT NULL,
            canonical_pcm_sha256 TEXT NOT NULL,
            alignment_audio_path TEXT NOT NULL,
            alignment_audio_sha256 TEXT NOT NULL,
            backend_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(recording_id) REFERENCES recordings(recording_id),
            FOREIGN KEY(result_run_id) REFERENCES runs(run_id)
        );
        """,
    ),
)
