# Browse existing recordings

Open **+ Add recording ? Browse existing recordings**. Check recordings, then use
**Add selected**. Ctrl/Shift highlight multiple rows and Space checks them together.
Selection is keyed by recording ID: multi-group appearances stay synchronized and
add once. Collapsed groups retain selection; the footer reports filtered-out selection.

Search covers titles, speakers, tags, and saved annotation labels (the currently
available transcript text). Language, speaker, collection/session and tag filters use
OR within a field and AND between fields. Each active value has a removable chip.
Clear filters also clears search. Sorting applies within each group. Group headers
and Left/Right keys expand independently. Expand all and Collapse all apply to the
current results. View preferences and expansion maps persist per grouping via QSettings.

Preview loads verified audio in a worker and plays it without adding a workstation
track. Stop preview and closing the browser invalidate pending preview loads. Details
show recording identity, revision identity, edit time, audio status and annotation labels.

## Optional metadata

File import and microphone capture include the same metadata editor. Use **Reuse
choice** or enter comma-separated values. Languages, speakers, collections and tags
support multiple values. Values normalize whitespace and case for consistent reusable
identities. Varieties use `language: dialect` labels. These are discovery labels;
annotation speaker references keep their existing independent meaning.

**Edit selected metadata** creates new immutable revisions. For multiple recordings,
only checked **Replace** fields change; an empty checked field clears that field.
Legacy language/default-speaker labels appear in the editor and are retained when
editing other fields. Missing metadata displays as **Unspecified**. Audio and annotation
content are preserved. Concurrent head changes require a refresh and retry; offline
saves retain the existing recovery workflow. Bulk saves report per-recording failures;
successful recordings remain saved if another recording fails.

## Persistence and deployment

Apply Alembic migration `20260915_0004_discovery` through the existing configured
PostgreSQL migration workflow before using this version. It adds a JSONB discovery
payload to each recording revision, defaulting old revisions to empty metadata.
Import commands, recovery envelopes, undo/redo, historical snapshots and ordinary
annotation saves carry that payload. The runtime database role needs its existing
revision INSERT/SELECT privileges; no UPDATE of historical revisions is introduced.

Catalog loading pages through all recordings in the background. Annotation labels are
read in one additional query per page. Filtering/grouping then operate on the loaded
catalog in memory. Extremely large catalogs may eventually need server-side paging
and a virtualized view; that is not implemented here.

## Future sound discovery

A future **Contains annotated sound** predicate must query annotation intervals and
return evidence with timestamps/previews. Recording tags are never evidence of an
annotated sound. Unannotated recordings have unknown sound coverage, not a negative
sound match. Recording-level filters are isolated in `recording_discovery.py` so this
interval query can be introduced as a separate application query.

## Validation

`tests/test_recording_discovery.py` exercises combined predicates, independent expansion,
keyboard controls, settings, hidden selection, duplicate appearances, add-once behavior,
preview cancellation, bulk edits, and immutable metadata/annotation preservation.
Microphone tests cover metadata through import and reopen. The PostgreSQL round-trip
test uses the generated disposable schema fixture and skips without TEST_DATABASE_URL.
