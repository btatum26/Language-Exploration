"""Focused speaker persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.errors import SpeakerNotFoundError
from models import Speaker
from persistence.sqlalchemy.mappers import speaker_from_row
from persistence.sqlalchemy.rows import SpeakerRow


class SpeakerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, speaker: Speaker) -> Speaker:
        metadata = speaker.model_dump(mode="json")["metadata"]
        row = SpeakerRow(
            id=speaker.id,
            external_key=speaker.external_key,
            display_name=speaker.display_name,
            metadata_json=metadata,
        )
        self._session.add(row)
        self._session.flush()
        return speaker_from_row(row)

    def get(self, speaker_id: UUID) -> Speaker:
        row = self._session.get(SpeakerRow, speaker_id)
        if row is None:
            raise SpeakerNotFoundError(f"speaker {speaker_id} was not found")
        return speaker_from_row(row)

    def list(self) -> tuple[Speaker, ...]:
        rows = self._session.scalars(
            select(SpeakerRow).order_by(SpeakerRow.display_name, SpeakerRow.id)
        )
        return tuple(speaker_from_row(row) for row in rows)
