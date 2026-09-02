from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from application.errors import DatabaseUnavailableError, PersistenceIntegrityError
from application.persistence import PersistenceStore
from models import (
    ConceptRef,
    PointGeometry,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeIntervalGeometry,
)
from persistence.sqlalchemy import __all__ as sqlalchemy_public_names
from persistence.sqlalchemy.mappers import geometry_from_columns, geometry_to_columns
from persistence.sqlalchemy.unit_of_work import SqlAlchemyPersistence


@pytest.mark.parametrize(
    "geometry",
    (
        PointGeometry(start_sample=10),
        TimeIntervalGeometry(start_sample=10, end_sample=20),
        TimeFrequencyBoxGeometry(
            start_sample=10,
            end_sample=20,
            min_frequency_hz=100,
            max_frequency_hz=500,
        ),
        TimeFrequencyPolygonGeometry(
            start_sample=10,
            end_sample=20,
            min_frequency_hz=100,
            max_frequency_hz=500,
            vertices=(
                {"sample": 10, "frequency_hz": 100},
                {"sample": 20, "frequency_hz": 100},
                {"sample": 15, "frequency_hz": 500},
            ),
        ),
    ),
    ids=("point", "interval", "box", "polygon"),
)
def test_geometry_maps_in_both_directions(geometry: object) -> None:
    columns = geometry_to_columns(geometry)  # type: ignore[arg-type]

    hydrated = geometry_from_columns(
        geometry_type=columns.geometry_type,
        start_sample=columns.start_sample,
        end_sample=columns.end_sample,
        min_frequency_hz=columns.min_frequency_hz,
        max_frequency_hz=columns.max_frequency_hz,
        polygon_vertices=columns.polygon_vertices,
    )

    assert hydrated == geometry


def test_concept_reference_components_reconstruct_portable_form() -> None:
    reference = ConceptRef("le.persistence@1.2.3:syllable")

    assert f"{reference.namespace}@{reference.version}:{reference.entry_key}" == str(reference)


def test_public_contract_and_sqlalchemy_exports_contain_no_rows_or_sessions() -> None:
    assert PersistenceStore.__module__ == "application.persistence"
    assert not any(name.endswith("Row") for name in sqlalchemy_public_names)
    assert "Session" not in sqlalchemy_public_names


def test_mapper_does_not_accept_unknown_geometry_discriminator() -> None:
    with pytest.raises(PersistenceIntegrityError, match="invalid persisted geometry"):
        geometry_from_columns(
            geometry_type=f"unknown-{uuid4()}",
            start_sample=0,
            end_sample=None,
            min_frequency_hz=None,
            max_frequency_hz=None,
            polygon_vertices=None,
        )


def test_connection_failures_are_translated_without_losing_the_cause() -> None:
    class UnavailableFactory:
        def __call__(self) -> object:
            raise OperationalError("connect", {}, OSError("offline"))

    store = SqlAlchemyPersistence(UnavailableFactory())  # type: ignore[arg-type]

    with pytest.raises(DatabaseUnavailableError) as captured:
        store.list_speakers()
    assert isinstance(captured.value.__cause__, OperationalError)
