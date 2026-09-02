"""Focused audio metadata persistence operations."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from application.errors import AudioAssetNotFoundError, PersistenceIntegrityError
from models import AudioAsset
from persistence.sqlalchemy.mappers import audio_asset_from_row, audio_asset_row_values
from persistence.sqlalchemy.rows import AudioAssetRow


class AudioAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register(self, audio_asset: AudioAsset) -> AudioAsset:
        matches = tuple(
            self._session.scalars(
                select(AudioAssetRow).where(
                    or_(
                        AudioAssetRow.id == audio_asset.id,
                        AudioAssetRow.sha256 == audio_asset.sha256,
                        AudioAssetRow.storage_uri == audio_asset.storage_uri,
                    )
                )
            )
        )
        if matches:
            if len(matches) != 1:
                raise PersistenceIntegrityError(
                    "audio identity, hash, and storage URI resolve to different assets"
                )
            existing = audio_asset_from_row(matches[0])
            if existing != audio_asset:
                raise PersistenceIntegrityError(
                    "registered audio metadata does not match the existing immutable asset"
                )
            return existing

        row = AudioAssetRow(**audio_asset_row_values(audio_asset))
        self._session.add(row)
        self._session.flush()
        return audio_asset_from_row(row)

    def get(self, audio_asset_id: UUID) -> AudioAsset:
        row = self._session.get(AudioAssetRow, audio_asset_id)
        if row is None:
            raise AudioAssetNotFoundError(f"audio asset {audio_asset_id} was not found")
        return audio_asset_from_row(row)
