import json
from pathlib import Path

from registry_align.schemas import canonical_registry_schema, output_schema


def test_checked_in_schemas_match_package_generators() -> None:
    root = Path(__file__).parents[2]

    assert (
        json.loads(
            (root / "schemas" / "canonical-registry.schema.json").read_text(encoding="utf-8")
        )
        == canonical_registry_schema()
    )
    assert json.loads((root / "schemas" / "output.schema.json").read_text(encoding="utf-8")) == (
        output_schema()
    )
