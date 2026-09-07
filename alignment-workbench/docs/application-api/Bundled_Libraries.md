# Bundled annotation libraries

Startup registers three immutable publications through `LibraryHandler` and the existing
SQLAlchemy store. All use the exact library version `0.1`; package and snapshot versions are
unchanged. Direct API clients can call `workbench.ensure_bundled_libraries()` explicitly.
`ensure_core_library()` uses the same loader and publication logic for core alone.

| Namespace | Entries | Geometry and scope |
| --- | --- | --- |
| `core` | 7 | `silence`, `unknown`, `word`, `syllable`, `noise`, `breath` are intervals; `marker` is a point |
| `phonetics` | 29 | Starter IPA sound intervals |
| `prosody` | 3 | `pitch-rise`, `pitch-fall`, `pitch-steady` are qualitative intervals |

Unannotated space has no assigned annotation. Silence is explicitly identified silence;
unknown is an examined interval whose content cannot be identified. Nothing fills gaps
automatically. Words and syllables require no parents, containment, or segmentation. Prosody
can overlap them and phonetic annotations freely; it requires no F0 measurements, thresholds,
or automatic classification. No analysis engine is part of library registration.

Use `SignalAnnotation.label` for occurrence text and `note` for free-form notes. The shipped
attribute schemas allow an empty object and no extra attributes. Neither field is duplicated
in attributes. Explicit nonblank labels take precedence in the GUI. Otherwise the exact pinned
definition supplies its IPA symbol or display name; an unresolved definition falls back to
the full concept reference. These display values are never written into annotation data.

## IPA starter inventory

The inventory is deliberately limited to:

```text
a e ɛ i o ɔ u
p b t d k ɡ f v s z ʃ ʒ
m n ɲ ŋ l ʎ r ɾ j w
```

Names and features were checked against the International Phonetic Association's
[2020 IPA chart](https://www.internationalphoneticassociation.org/IPAcharts/common_files/pdfs/pdfs_IPA_charts_archive/IPA_unitipa_2020.pdf).
Vowels record height, backness, and rounding. Consonants record voicing, place, and manner.
`a` uses the chart's open front unrounded value; `t` and `d` use the alveolar starter
interpretation of the chart's dental/alveolar region. `w` is the voiced labial-velar
approximant from Other Symbols. This is not complete IPA coverage. Diacritic composition,
full-chart coverage, and a detailed taxonomy are deferred.

ASCII keys equal the symbol where possible. Exceptions are `epsilon` (ɛ), `open-o` (ɔ),
`g` (ɡ), `esh` (ʃ), `ezh` (ʒ), `palatal-n` (ɲ), `eng` (ŋ), `palatal-l` (ʎ), and `tap` (ɾ).
For example, `phonetics@0.1:epsilon` is a stable reference; the symbol is metadata, not a key.
Every sound includes a readable name, category, features, aliases, source, and display hints.

## Authored files and validation

Resources live in `libraries/<namespace>/<version>/`, resolved relative to application code,
independently of the process working directory. This flat uv project runs from its checkout;
distributions must include the `libraries` directory alongside `src`.

`manifest.json` contains exactly `namespace`, `name`, `version`, and `purpose`. The namespace
is the stable library identity, not a generated UUID. `definitions.json` is an ordered array
of objects using `entry_key`, `display_name`, `description`, `allowed_geometry_types`,
`attribute_schema`, `validation_hints`, `display_hints`, and `metadata`. Authored definitions
contain no database IDs or publication timestamps. Entry ordering participates in the hash.

The optional `categories.json` maps shallow category keys to `{ "name": "...",
"description": "..." }` descriptors. An entry references its category with
`metadata.category`; loading copies the descriptor into `metadata.category_descriptor`
before hashing and publication. Thus retrieval through the ordinary API preserves category
information, including when source files are unavailable to the client.

Loading validates manifest grammar and directory agreement, duplicate keys, domain fields,
geometry values, JSON Schemas, metadata types, and category references. All three bundles
are validated before any publication writes. Hashing uses `library_content_sha256()` and
its existing canonicalization; generated IDs and timestamps are excluded.

Repeated startup reuses persisted versions. Unique constraints arbitrate competing
initializers, which reread and verify the winning content. A rejected write with no matching
winner propagates as a persistence failure. Same-version content mismatches stop startup
without changing stored content. Identical content under a different version also conflicts
with the existing unique-content constraint. Publication operations are individually atomic;
startup can safely resume after partial registration.

After release, any changed entry, ordering, or expanded category definition requires a new
library version and directory. Do not edit a published version in place. Recording revisions
retain their exact namespace, version, and content-hash pins. The explicit placeholder
transition below is a one-time exception, not normal publication behavior.

## Transition from the test-only core placeholder

An existing `core@0.1` containing only `test` conflicts with the requested core publication.
Startup identifies that placeholder and preserves its database, recordings, and snapshots.
It does not overwrite it, rename this bundle, run migrations, or reset a database.

### Keep the current database and convert test annotations to words

When deliberately replacing the original development placeholder, use the owner-only
maintenance command below. It accepts only the exact original test-only definition and
hash, or an already-migrated bundle. It is never invoked automatically by startup.

Close the workbench and resolve any pending, conflicted, or quarantined recovery files.
Back up the database with `pg_dump` before applying. From `alignment-workbench`:

```powershell
# Read-only inspection of the configured database:
uv run python scripts/migrate_core_placeholder.py

# Explicit data migration, with a new backup filename:
uv run python scripts/migrate_core_placeholder.py --apply --backup .artifacts/core-transition/schema-rows-before.json

# Register the other bundles and verify startup:
uv run python src/cli_main.py
```

Both configured database URLs must point to the same target. The command uses
`REGISTRY_ALIGN_DATABASE_ADMIN_URL` and owns its SSH tunnel. No new Alembic revision or
database target is required; the current schema must already include the label migration.

The transaction locks the nine workbench tables, writes and verifies a complete JSON
backup of their rows, changes the referenced `test` entry into the bundled `word` entry
while retaining its UUID, inserts the other six core entries, and updates the canonical
content hash. The library/version UUIDs remain unchanged. Every annotation row, recording,
revision, pin link, audio record, and speaker record is compared with the backup before
commit. Incompatible nonempty occurrence attributes are rejected instead of discarded.

Consequently, both current and historical annotations resolve as `core@0.1:word`, preserving
their IDs, timing, labels, notes, and other fields. Historical database pins resolve to the
replacement core content/hash too. This intentionally changes the interpretation of old
revisions. Previously exported snapshots and archived recovery files are not rewritten and
retain the old references/hash. Normal runtime-role immutability protections remain intact.

Repeat inspection reports `already_migrated`. The row backup is an additional forensic/data
backup, not a replacement for `pg_dump` with schema definitions and grants. Restore from
the full database backup only as an explicit recovery operation, preferably into a separate
target for inspection first.

### Preserve the placeholder in a separate development target

Alternatively, create a separate database. On the PostgreSQL server, as a role with
`CREATEDB`, run this SQL outside a transaction (the existing owner/runtime roles are reused):

```sql
CREATE DATABASE alignment_workbench_bundles_dev OWNER registry_align_owner;
```

In `alignment-workbench/.env`, select the new database in **both** URLs. Replace `PASSWORD`
locally with the appropriate role credentials; retain the existing SSH endpoint if different.
Use separate local storage roots so recovery files from the old database are not reused:

```dotenv
REGISTRY_ALIGN_DATABASE_URL=postgresql+psycopg://registry_align_app:PASSWORD@127.0.0.1:5433/alignment_workbench_bundles_dev
REGISTRY_ALIGN_DATABASE_ADMIN_URL=postgresql+psycopg://registry_align_owner:PASSWORD@127.0.0.1:5433/alignment_workbench_bundles_dev
ALIGNMENT_WORKBENCH_AUDIO_ROOT=C:\workbench-bundles-dev\audio
ALIGNMENT_WORKBENCH_RECOVERY_ROOT=C:\workbench-bundles-dev\recovery
```

Remove stale process-level overrides if using `.env`, then initialize the fresh target:

```powershell
Set-Location C:\Users\coole\Documents\Language-Exploration\alignment-workbench
Remove-Item Env:REGISTRY_ALIGN_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:REGISTRY_ALIGN_DATABASE_ADMIN_URL -ErrorAction SilentlyContinue
Remove-Item Env:ALIGNMENT_WORKBENCH_AUDIO_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:ALIGNMENT_WORKBENCH_RECOVERY_ROOT -ErrorAction SilentlyContinue
$env:UV_CACHE_DIR = '.uv-cache'
uv run alembic -c .\alembic.ini upgrade head
uv run alembic -c .\alembic.ini current
```

Expected migration head is `20260906_0003_annotation_label`. As `registry_align_owner`,
connected specifically to `alignment_workbench_bundles_dev`, execute
[`sql/configure_runtime_role.sql`](../../sql/configure_runtime_role.sql) to install the
existing runtime grants. For example, in a local psql session already connected to that target:

```text
\conninfo
\i 'C:/Users/coole/Documents/Language-Exploration/alignment-workbench/sql/configure_runtime_role.sql'
```

Then verify registration and open the GUI:

```powershell
uv run python src/cli_main.py
uv run python src/main.py
```

The CLI should report `libraries=3`, Core annotations with 7 entries, Phonetics with 29,
and Prosody with 3; each has `latest_version=0.1`. Running it again must give the same
publication IDs. Alembic and application commands each own and close their SSH tunnel;
leave the local port free between commands. The original database remains available by
restoring its URLs and storage settings with an application compatible with its publications.

## GUI use

1. Import a WAV or MP3. The initial revision pins core automatically. Opening an existing
   recording adds nothing; **Use core library** explicitly pins core when needed.
2. Select **Phonetics** in Libraries and click **Pin latest**. Repeat for **Prosody**.
   Each list row shows version and entry count. Pinning is an undoable edit; save to persist it.
3. In **New annotation**, search a name, ASCII key, IPA symbol, or alias. Use **Category**
   to filter vowels/consonants. Choices come only from the recording's pinned definitions.
4. Select an interval, choose a sound/core/prosody concept, optionally enter Label/Note,
   and click **Create annotation**. Permitted geometry comes from the definition.
5. For a marker, choose **marker**, click the waveform to choose a sample or enter
   **Start sample** explicitly, then click **Create annotation**. No interval is required;
   valid samples satisfy `0 <= start_sample < frame_count`. Its narrow lane bar and table
   row are selectable. Change its position through the sample editor.
6. Use **Undo**, **Redo**, and **Save Revision**. Close/reopen to restore exact references,
   pins, labels, and geometry. Interval boundary dragging and audio controls remain available.

## Verification

From `alignment-workbench`:

```powershell
uv run pytest --basetemp=.pytest-bundles
uv run ruff check src tests
uv run mypy --python-version 3.12 src
```

The installed NumPy stubs require Python 3.12 syntax for mypy; plain mypy with the project's
3.11 target currently fails in those dependency stubs. This does not change the application
runtime requirement. GUI checks use the existing offscreen Qt test setup. PostgreSQL checks
require an explicitly disposable `TEST_DATABASE_URL`; they never fall back to the application
URL. The bundle persistence test reuses the existing isolated-schema fixture in
`tests/database/test_core_first_use_postgresql.py`. See the
[database test instructions](../testing/Data_Model_and_Persistence_Testing.md).
