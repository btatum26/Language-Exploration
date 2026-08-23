"""Transactional SQLite repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.segments import AlignmentSegment, SegmentRevision
from registry_align.domain.transcripts import NormalizedTranscript
from registry_align.events import Issue
from registry_align.storage.migrations import MIGRATIONS
from registry_align.storage.repository import CacheRecord


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, script in MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )
            connection.commit()

    def start_run(
        self,
        run_id: str,
        registry_path: Path,
        config_fingerprint: str,
        backend_name: str,
        backend_version: str,
        model_id: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id,status,started_at,registry_path,config_fingerprint,"
                "backend_name,backend_version,model_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    "running",
                    _now(),
                    str(registry_path),
                    config_fingerprint,
                    backend_name,
                    backend_version,
                    model_id,
                ),
            )
            connection.commit()

    def finish_run(self, run_id: str, status: str, counts: dict[str, int]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status=?, finished_at=?, counts_json=? WHERE run_id=?",
                (status, _now(), json.dumps(counts, sort_keys=True), run_id),
            )
            connection.commit()

    def save_recording(
        self, entry: RegistryEntry, prepared: PreparedRecording, run_id: str
    ) -> None:
        values = (
            entry.id,
            str(entry.source_registry_path),
            entry.source_entry_index,
            entry.audio_relative_path,
            str(entry.audio_resolved_path),
            entry.transcript_raw,
            entry.language,
            entry.speaker_id,
            json.dumps(entry.metadata, ensure_ascii=False, sort_keys=True),
            int(entry.id_was_derived),
            prepared.source_audio_sha256,
            prepared.source_codec,
            prepared.source_sample_rate_hz,
            prepared.source_channels,
            prepared.source_duration_s,
            str(prepared.canonical_pcm_path),
            prepared.canonical_pcm_sha256,
            prepared.canonical_sample_rate_hz,
            prepared.canonical_channels,
            prepared.canonical_frame_count,
            str(prepared.alignment_audio_path),
            prepared.alignment_audio_sha256,
            prepared.alignment_sample_rate_hz,
            run_id,
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    source_registry_path=excluded.source_registry_path,
                    source_entry_index=excluded.source_entry_index,
                    audio_relative_path=excluded.audio_relative_path,
                    audio_resolved_path=excluded.audio_resolved_path,
                    transcript_raw=excluded.transcript_raw,
                    language=excluded.language,
                    speaker_id=excluded.speaker_id,
                    metadata_json=excluded.metadata_json,
                    id_was_derived=excluded.id_was_derived,
                    source_audio_sha256=excluded.source_audio_sha256,
                    source_codec=excluded.source_codec,
                    source_sample_rate_hz=excluded.source_sample_rate_hz,
                    source_channels=excluded.source_channels,
                    source_duration_s=excluded.source_duration_s,
                    canonical_pcm_path=excluded.canonical_pcm_path,
                    canonical_pcm_sha256=excluded.canonical_pcm_sha256,
                    canonical_sample_rate_hz=excluded.canonical_sample_rate_hz,
                    canonical_channels=excluded.canonical_channels,
                    canonical_frame_count=excluded.canonical_frame_count,
                    alignment_audio_path=excluded.alignment_audio_path,
                    alignment_audio_sha256=excluded.alignment_audio_sha256,
                    alignment_sample_rate_hz=excluded.alignment_sample_rate_hz,
                    latest_run_id=excluded.latest_run_id
                """,
                values,
            )
            connection.commit()

    def save_discovered_entry(self, entry: RegistryEntry, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO recordings(
                    recording_id,source_registry_path,source_entry_index,audio_relative_path,
                    audio_resolved_path,transcript_raw,language,speaker_id,metadata_json,
                    id_was_derived,latest_run_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    source_registry_path=excluded.source_registry_path,
                    source_entry_index=excluded.source_entry_index,
                    audio_relative_path=excluded.audio_relative_path,
                    audio_resolved_path=excluded.audio_resolved_path,
                    transcript_raw=excluded.transcript_raw,
                    language=excluded.language,
                    speaker_id=excluded.speaker_id,
                    metadata_json=excluded.metadata_json,
                    id_was_derived=excluded.id_was_derived,
                    latest_run_id=excluded.latest_run_id
                """,
                (
                    entry.id,
                    str(entry.source_registry_path),
                    entry.source_entry_index,
                    entry.audio_relative_path,
                    str(entry.audio_resolved_path),
                    entry.transcript_raw,
                    entry.language,
                    entry.speaker_id,
                    json.dumps(entry.metadata, ensure_ascii=False, sort_keys=True),
                    int(entry.id_was_derived),
                    run_id,
                ),
            )
            connection.commit()

    def point_recording_to_run(self, recording_id: str, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE recordings SET latest_run_id=? WHERE recording_id=?",
                (run_id, recording_id),
            )
            connection.commit()

    def save_transcript(self, run_id: str, transcript: NormalizedTranscript) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    transcript.recording_id,
                    transcript.raw_text,
                    transcript.normalized_text,
                    transcript.raw_sha256,
                    transcript.normalized_sha256,
                    transcript.normalizer_name,
                    transcript.normalizer_version,
                    json.dumps(transcript.warnings, ensure_ascii=False),
                ),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO source_tokens VALUES (?,?,?,?,?)",
                [
                    (run_id, transcript.recording_id, token.token_id, token.index, token.text)
                    for token in transcript.source_tokens
                ],
            )
            connection.executemany(
                "INSERT OR REPLACE INTO alignment_tokens VALUES (?,?,?,?,?)",
                [
                    (run_id, transcript.recording_id, token.token_id, token.index, token.text)
                    for token in transcript.alignment_tokens
                ],
            )
            connection.execute(
                "DELETE FROM token_mappings WHERE run_id=? AND recording_id=?",
                (run_id, transcript.recording_id),
            )
            connection.executemany(
                "INSERT INTO token_mappings(run_id,recording_id,source_token_ids_json,"
                "alignment_token_ids_json,relationship) VALUES (?,?,?,?,?)",
                [
                    (
                        run_id,
                        transcript.recording_id,
                        json.dumps(mapping.source_token_ids),
                        json.dumps(mapping.alignment_token_ids),
                        mapping.relationship,
                    )
                    for mapping in transcript.token_mappings
                ],
            )
            connection.commit()

    def save_segments(self, segments: tuple[AlignmentSegment, ...]) -> None:
        if not segments:
            return
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO segments VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        item.segment_id,
                        item.run_id,
                        item.recording_id,
                        item.tier,
                        item.label,
                        item.backend_label,
                        item.start_s,
                        item.end_s,
                        item.start_sample,
                        item.end_sample,
                        item.timebase_sample_rate_hz,
                        item.parent_segment_id,
                        json.dumps(item.source_token_ids),
                        item.confidence,
                        item.review_status,
                        item.source,
                        item.model_id,
                        item.notes,
                    )
                    for item in segments
                ],
            )
            connection.commit()

    def save_revision(self, revision: SegmentRevision) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO segment_revisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    revision.revision_id,
                    revision.segment_id,
                    revision.base_run_id,
                    revision.start_sample_override,
                    revision.end_sample_override,
                    revision.label_override,
                    revision.review_status,
                    revision.author,
                    revision.created_at.isoformat(),
                    revision.reason,
                ),
            )
            connection.commit()

    def save_issue(self, run_id: str, issue: Issue) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO issues(run_id,recording_id,code,severity,stage,message,hint,"
                "details_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    issue.recording_id,
                    issue.code,
                    issue.severity.value,
                    issue.stage,
                    issue.message,
                    issue.hint,
                    json.dumps(issue.details, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()

    def save_artifact(
        self,
        run_id: str,
        kind: str,
        path: Path,
        *,
        recording_id: str | None = None,
        sha256: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO artifacts(run_id,recording_id,kind,path,sha256,details_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    recording_id,
                    kind,
                    str(path),
                    sha256,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()

    def get_cache(self, cache_key: str) -> CacheRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key=? AND status='complete'", (cache_key,)
            ).fetchone()
        if row is None:
            return None
        return CacheRecord(
            cache_key=row["cache_key"],
            recording_id=row["recording_id"],
            result_run_id=row["result_run_id"],
            status=row["status"],
            canonical_pcm_path=Path(row["canonical_pcm_path"]),
            canonical_pcm_sha256=row["canonical_pcm_sha256"],
            alignment_audio_path=Path(row["alignment_audio_path"]),
            alignment_audio_sha256=row["alignment_audio_sha256"],
            backend_fingerprint=row["backend_fingerprint"],
        )

    def save_cache(
        self,
        cache_key: str,
        run_id: str,
        prepared: PreparedRecording,
        backend_fingerprint: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cache_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    cache_key,
                    prepared.recording_id,
                    run_id,
                    "complete",
                    str(prepared.canonical_pcm_path),
                    prepared.canonical_pcm_sha256,
                    str(prepared.alignment_audio_path),
                    prepared.alignment_audio_sha256,
                    backend_fingerprint,
                    _now(),
                ),
            )
            connection.commit()

    def latest_run(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def status_counts(self) -> dict[str, int]:
        run = self.latest_run()
        if run is None:
            return {}
        counts = json.loads(run["counts_json"])
        return {str(key): int(value) for key, value in counts.items()}

    def effective_recordings(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM recordings ORDER BY source_entry_index"
            ).fetchall()
        return [dict(row) for row in rows]

    def effective_segments(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM segments s
                JOIN recordings r ON r.recording_id=s.recording_id AND r.latest_run_id=s.run_id
                ORDER BY r.source_entry_index, s.start_sample, s.tier
                """
            ).fetchall()
            revision_rows = connection.execute(
                """
                SELECT sr.* FROM segment_revisions sr
                JOIN (
                    SELECT segment_id, MAX(created_at) AS latest_created_at
                    FROM segment_revisions
                    WHERE review_status='accepted'
                    GROUP BY segment_id
                ) latest
                  ON latest.segment_id=sr.segment_id
                 AND latest.latest_created_at=sr.created_at
                WHERE sr.review_status='accepted'
                """
            ).fetchall()
        revisions = {str(row["segment_id"]): row for row in revision_rows}
        effective: list[dict[str, Any]] = []
        for source_row in rows:
            row = dict(source_row)
            row["model_start_sample"] = row["start_sample"]
            row["model_end_sample"] = row["end_sample"]
            row["model_label"] = row["label"]
            row["effective_revision_id"] = None
            revision = revisions.get(str(row["segment_id"]))
            if revision is not None:
                row["effective_revision_id"] = revision["revision_id"]
                if revision["start_sample_override"] is not None:
                    row["start_sample"] = revision["start_sample_override"]
                    row["start_s"] = row["start_sample"] / row["timebase_sample_rate_hz"]
                if revision["end_sample_override"] is not None:
                    row["end_sample"] = revision["end_sample_override"]
                    row["end_s"] = row["end_sample"] / row["timebase_sample_rate_hz"]
                if revision["label_override"] is not None:
                    row["label"] = revision["label_override"]
            effective.append(row)
        return effective

    def all_issues(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if run_id is None:
                rows = connection.execute("SELECT * FROM issues ORDER BY issue_id").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM issues WHERE run_id=? ORDER BY issue_id", (run_id,)
                ).fetchall()
        return [dict(row) for row in rows]

    def effective_issues(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            latest = connection.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            latest_run_id = latest[0] if latest is not None else ""
            rows = connection.execute(
                """
                SELECT i.* FROM issues i
                LEFT JOIN recordings r ON r.recording_id=i.recording_id
                WHERE (i.recording_id IS NOT NULL AND r.latest_run_id=i.run_id)
                   OR (i.recording_id IS NULL AND i.run_id=?)
                ORDER BY i.issue_id
                """,
                (latest_run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
