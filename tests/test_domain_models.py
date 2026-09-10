"""Unit test untuk model domain (task 2.1).

Memverifikasi: dataclass frozen (immutable), default kosong untuk
``Snapshot.links``/``Snapshot.image_hashes``, dan default kosong untuk field
list pada ``Diff``. Serialisasi diuji terpisah pada task 2.2.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from monitoring.domain.models import (
    Alert,
    ChangeEvent,
    ChangeSummary,
    Diff,
    Snapshot,
    WebsiteConfig,
)


def test_website_config_fields():
    now = datetime(2020, 1, 1, 12, 0, 0)
    cfg = WebsiteConfig(
        id="id-1",
        domain="example.com",
        name="Example",
        poll_interval_seconds=None,
        created_at=now,
    )
    assert cfg.id == "id-1"
    assert cfg.domain == "example.com"
    assert cfg.name == "Example"
    assert cfg.poll_interval_seconds is None
    assert cfg.created_at == now


def test_snapshot_defaults_empty_links_and_image_hashes():
    snap = Snapshot(
        url="https://example.com/",
        website_id="id-1",
        normalized_text="hello world",
        content_hash="abc123",
        checked_at=datetime(2020, 1, 1),
    )
    assert snap.links == []
    assert snap.image_hashes == {}


def test_snapshot_default_collections_are_independent_per_instance():
    a = Snapshot("u1", "w", "t", "h", datetime(2020, 1, 1))
    b = Snapshot("u2", "w", "t", "h", datetime(2020, 1, 1))
    # Default factory harus menghasilkan objek baru, bukan berbagi referensi.
    assert a.links is not b.links
    assert a.image_hashes is not b.image_hashes


def test_snapshot_accepts_explicit_links_and_hashes():
    snap = Snapshot(
        url="https://example.com/",
        website_id="id-1",
        normalized_text="text",
        content_hash="hash",
        checked_at=datetime(2020, 1, 1),
        links=["https://example.com/a"],
        image_hashes={"https://example.com/img.png": "imghash"},
    )
    assert snap.links == ["https://example.com/a"]
    assert snap.image_hashes == {"https://example.com/img.png": "imghash"}


def test_snapshot_is_frozen():
    snap = Snapshot("u", "w", "t", "h", datetime(2020, 1, 1))
    with pytest.raises(FrozenInstanceError):
        snap.content_hash = "other"  # type: ignore[misc]


def test_diff_defaults_all_lists_empty():
    diff = Diff()
    assert diff.text_added == []
    assert diff.text_removed == []
    assert diff.links_added == []
    assert diff.links_removed == []
    assert diff.sections_added == []
    assert diff.sections_removed == []
    assert diff.images_added == []
    assert diff.images_removed == []
    assert diff.images_changed == []
    assert diff.title_changed is None
    assert diff.meta_changed is None


def test_diff_accepts_title_and_meta_changed():
    diff = Diff(
        title_changed=("Lama", "Baru"),
        meta_changed=("Meta lama", "Meta baru"),
    )
    assert diff.title_changed == ("Lama", "Baru")
    assert diff.meta_changed == ("Meta lama", "Meta baru")


def test_snapshot_defaults_title_and_meta_description_none():
    snap = Snapshot(
        url="https://example.com/",
        website_id="id-1",
        normalized_text="hello",
        content_hash="hash",
        checked_at=datetime(2020, 1, 1),
    )
    assert snap.title is None
    assert snap.meta_description is None


def test_snapshot_accepts_explicit_title_and_meta_description():
    snap = Snapshot(
        url="https://example.com/",
        website_id="id-1",
        normalized_text="hello",
        content_hash="hash",
        checked_at=datetime(2020, 1, 1),
        title="Judul Halaman",
        meta_description="Deskripsi halaman",
    )
    assert snap.title == "Judul Halaman"
    assert snap.meta_description == "Deskripsi halaman"


def test_change_summary_fields():
    summary = ChangeSummary(
        text_added=3,
        text_removed=1,
        links_added=2,
        links_removed=0,
        images_changed=1,
    )
    assert summary.text_added == 3
    assert summary.text_removed == 1
    assert summary.links_added == 2
    assert summary.links_removed == 0
    assert summary.images_changed == 1


def test_change_event_holds_diff_and_summary():
    diff = Diff(text_added=["new line"])
    summary = ChangeSummary(1, 0, 0, 0, 0)
    now = datetime(2020, 1, 1, 8, 30, 0)
    event = ChangeEvent(
        id="evt-1",
        website_id="id-1",
        url="https://example.com/page",
        detected_at=now,
        diff=diff,
        summary=summary,
    )
    assert event.diff is diff
    assert event.summary is summary
    assert event.detected_at == now


def test_change_event_is_frozen():
    event = ChangeEvent(
        id="evt-1",
        website_id="id-1",
        url="https://example.com/page",
        detected_at=datetime(2020, 1, 1),
        diff=Diff(),
        summary=ChangeSummary(0, 0, 0, 0, 0),
    )
    with pytest.raises(FrozenInstanceError):
        event.url = "https://other.com"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Alert (ContentMonitor Tahap 3)
# --------------------------------------------------------------------------- #
def test_alert_fields_and_defaults():
    now = datetime(2024, 1, 1, 12, 0, 0)
    alert = Alert(
        id="a1",
        website_id="w1",
        alert_type="page_unreachable",
        severity="critical",
        title="Halaman tidak dapat diakses",
        triggered_at=now,
    )
    assert alert.id == "a1"
    assert alert.website_id == "w1"
    assert alert.url is None
    assert alert.detail is None
    assert alert.status == "unread"


def test_alert_accepts_explicit_url_detail_and_status():
    alert = Alert(
        id="a1",
        website_id="w1",
        alert_type="title_changed",
        severity="info",
        title="Judul berubah",
        triggered_at=datetime(2024, 1, 1),
        url="https://example.com/page",
        detail="Lama -> Baru",
        status="read",
    )
    assert alert.url == "https://example.com/page"
    assert alert.detail == "Lama -> Baru"
    assert alert.status == "read"


def test_alert_is_frozen():
    alert = Alert(
        id="a1",
        website_id="w1",
        alert_type="page_unreachable",
        severity="critical",
        title="Judul",
        triggered_at=datetime(2024, 1, 1),
    )
    with pytest.raises(FrozenInstanceError):
        alert.status = "read"  # type: ignore[misc]
