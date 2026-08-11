"""Every archived vendor document must carry provenance a reader can check.

An archive whose origin nobody recorded is worth very little — you cannot tell whether a
"vendor specification" came from the vendor or from a forum post. These tests make the
rule mechanical rather than aspirational.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml

from carle.table import repo_root

MANIFEST = repo_root() / "official-docs" / "manifest.yaml"
ARCHIVE_DIR = repo_root() / "official-docs"

REQUIRED = ("source_url", "retrieved", "description")
ALLOWED = {*REQUIRED, "local_path", "capture", "retrieval_failed"}
CAPTURES = ("verbatim", "extracted")


@pytest.fixture(scope="module")
def documents() -> list[dict]:
    raw = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "manifest must be a mapping"
    docs = raw.get("documents")
    assert isinstance(docs, list) and docs, "manifest must carry a non-empty 'documents' list"
    return docs


def test_every_entry_has_a_source_url_and_retrieval_date(documents):
    for entry in documents:
        for key in REQUIRED:
            assert entry.get(key), f"{entry.get('source_url', entry)} is missing {key}"


def test_retrieved_dates_are_iso_8601(documents):
    for entry in documents:
        retrieved = entry["retrieved"]
        if isinstance(retrieved, dt.date):
            continue
        dt.date.fromisoformat(str(retrieved))


def test_source_urls_are_absolute(documents):
    for entry in documents:
        assert str(entry["source_url"]).startswith("http"), entry["source_url"]


def test_no_unknown_fields(documents):
    for entry in documents:
        unknown = set(entry) - ALLOWED
        assert not unknown, f"unknown field(s) {unknown} on {entry['source_url']}"


def test_capture_mode_is_declared_for_archived_files(documents):
    """An extracted transcription must not be mistaken for a byte-for-byte archive."""
    for entry in documents:
        if entry.get("local_path"):
            assert entry.get("capture") in CAPTURES, (
                f"{entry['local_path']} must declare capture as one of {CAPTURES}"
            )


def test_every_local_path_exists(documents):
    for entry in documents:
        local_path = entry.get("local_path")
        if local_path:
            assert (repo_root() / local_path).exists(), f"{local_path} does not exist"


def test_every_local_path_stays_inside_official_docs(documents):
    """`repo_root() / local_path` drops the root for an absolute path, so an entry could
    claim a vendor document is archived here while pointing at a file elsewhere."""
    archive_root = ARCHIVE_DIR.resolve()
    for entry in documents:
        local_path = entry.get("local_path")
        if not local_path:
            continue
        assert not Path(local_path).is_absolute(), f"{local_path} is an absolute path"
        resolved = (repo_root() / local_path).resolve()
        assert resolved.is_relative_to(archive_root), f"{local_path} resolves outside {ARCHIVE_DIR}"
        assert resolved.is_file(), f"{local_path} is not a file"


def test_every_archived_file_is_accounted_for(documents):
    """Nothing sits in official-docs/ without provenance — the rule that gives the
    directory its meaning."""
    recorded = {
        (repo_root() / entry["local_path"]).resolve()
        for entry in documents
        if entry.get("local_path")
    }
    # rglob, not iterdir: a nested archive directory would otherwise carry no provenance
    # and no test would complain.
    on_disk = {
        path.resolve()
        for path in ARCHIVE_DIR.rglob("*")
        if path.is_file() and path.name != "manifest.yaml"
    }
    assert on_disk - recorded == set(), (
        f"files in official-docs/ with no manifest entry: "
        f"{sorted(p.name for p in on_disk - recorded)}"
    )


def test_failed_retrievals_omit_local_path(documents):
    """A source that could not be reached is recorded, not silently dropped."""
    for entry in documents:
        if entry.get("retrieval_failed"):
            assert not entry.get("local_path"), (
                f"{entry['source_url']} records a failed retrieval but names a local file"
            )


def test_entries_without_a_local_path_explain_why(documents):
    for entry in documents:
        if not entry.get("local_path"):
            assert entry.get("retrieval_failed"), (
                f"{entry['source_url']} has no local_path and no retrieval_failed reason"
            )


def test_the_user_manual_is_archived(documents):
    """Regression guard: the manual arrived in the tree before provenance was recorded."""
    manual = [e for e in documents if str(e.get("local_path", "")).endswith("User-Manual.pdf")]
    assert manual, "the Ruko user manual has no manifest entry"
    assert manual[0]["source_url"].startswith("https://rukotoy.com")
