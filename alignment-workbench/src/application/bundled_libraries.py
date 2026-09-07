"""Validate declarative resources before publishing immutable library versions."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict

from application.errors import WorkbenchStartupError
from application.validation import library_content_sha256
from models import Library, LibraryEntry, LibraryVersion, Namespace, VersionLabel

BUNDLE_ROOT = Path(__file__).resolve().parents[2] / "libraries"


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Namespace
    name: str
    version: VersionLabel
    purpose: str


def load_bundle(path: Path, library_id: UUID | None = None) -> tuple[Library, LibraryVersion]:
    """Expand reusable category descriptors into persisted metadata before hashing."""
    try:
        manifest = Manifest.model_validate_json((path / "manifest.json").read_text("utf-8"))
        if (manifest.namespace, manifest.version) != (path.parent.name, path.name):
            raise ValueError("manifest namespace/version must agree with its directory")
        library = Library(
            id=library_id or uuid4(),
            namespace=manifest.namespace,
            name=manifest.name,
            description=manifest.purpose,
        )
        category_path = path / "categories.json"
        categories = json.loads(category_path.read_text("utf-8")) if category_path.exists() else {}
        if not isinstance(categories, dict) or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, dict)
            or set(value) != {"name", "description"}
            or any(not isinstance(v, str) or not v.strip() for v in value.values())
            for key, value in categories.items()
        ):
            raise ValueError("categories must map keys to name/description descriptors")
        definitions = json.loads((path / "definitions.json").read_text("utf-8"))
        if not isinstance(definitions, list) or not definitions:
            raise ValueError("definitions must be a nonempty ordered array")
        version_id = uuid4()
        entries = []
        for definition in definitions:
            if not isinstance(definition, dict) or set(definition) - (
                set(LibraryEntry.model_fields) - {"id", "library_version_id"}
            ):
                raise ValueError("definition contains unsupported fields or generated IDs")
            metadata = definition.get("metadata", {}).copy()
            category = metadata.get("category")
            if category is not None:
                if not isinstance(category, str) or category not in categories:
                    raise ValueError(f"unknown category {category!r}")
                metadata["category_descriptor"] = categories[category]
            elif categories:
                raise ValueError("entry is missing its category reference")
            if "ipa_symbol" in metadata:
                if not isinstance(metadata["ipa_symbol"], str) or not metadata["ipa_symbol"]:
                    raise ValueError("ipa_symbol must be nonempty text")
                if not isinstance(metadata.get("features"), dict):
                    raise ValueError("IPA entries require features")
            if "aliases" in metadata and (
                not isinstance(metadata["aliases"], list)
                or any(not isinstance(alias, str) for alias in metadata["aliases"])
            ):
                raise ValueError("aliases must be an array of strings")
            entry = LibraryEntry(
                id=uuid4(), library_version_id=version_id, **{**definition, "metadata": metadata}
            )
            schema = entry.model_dump(mode="json")["attribute_schema"]
            validator_for(schema).check_schema(schema)
            entries.append(entry)
        if len({entry.entry_key for entry in entries}) != len(entries):
            raise ValueError("duplicate entry keys")
        version = LibraryVersion(
            id=version_id,
            library_id=library.id,
            version_label=manifest.version,
            content_sha256="0" * 64,
            created_at=datetime.now(UTC),
            entries=tuple(entries),
        )
        return library, version.model_copy(
            update={"content_sha256": library_content_sha256(version)}
        )
    except (ValueError, TypeError, AttributeError, OSError, SchemaError) as exc:
        raise WorkbenchStartupError(f"Invalid bundled library at {path}: {exc}") from exc
