"""Test serialisasi JSON model domain (task 2.2).

Berisi:

- Property-based test round-trip (Hypothesis, min 100 iterasi) untuk ``Snapshot``
  yang memvalidasi Property 14 pada design (round-trip serialisasi Snapshot,
  termasuk mempertahankan link & Image_Hash kosong).
- Round-trip serialisasi untuk ``Diff`` dan ``ChangeEvent`` sesuai cakupan
  task 2.2.
- Unit test contoh spesifik untuk kasus penting (koleksi kosong, presisi
  datetime).

Semua round-trip menuntut ``deserialize(serialize(x)) == x``.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from monitoring.domain.models import Alert, ChangeEvent, ChangeSummary, Diff, Snapshot
from monitoring.domain.serialization import (
    deserialize_alert,
    deserialize_change_event,
    deserialize_diff,
    deserialize_snapshot,
    serialize_alert,
    serialize_change_event,
    serialize_diff,
    serialize_snapshot,
)

# --------------------------------------------------------------------------- #
# Strategi generator
# --------------------------------------------------------------------------- #
# Naive datetime agar isoformat/fromisoformat bersifat lossless di Python 3.9.
_datetimes = st.datetimes(
    min_value=datetime(1970, 1, 1),
    max_value=datetime(2100, 1, 1),
)

_text = st.text(max_size=200)
_url = st.text(min_size=1, max_size=100)


_optional_text = st.none() | st.text(max_size=200)
_optional_pair = st.none() | st.tuples(st.text(max_size=50), st.text(max_size=50))


def snapshots(min_links: int = 0, min_hashes: int = 0) -> st.SearchStrategy:
    """Strategi Snapshot valid, mencakup link/image_hashes kosong & non-kosong."""
    return st.builds(
        Snapshot,
        url=_url,
        website_id=_text,
        normalized_text=_text,
        content_hash=_text,
        checked_at=_datetimes,
        links=st.lists(_url, min_size=min_links, max_size=10),
        image_hashes=st.dictionaries(_url, _text, min_size=min_hashes, max_size=10),
        title=_optional_text,
        meta_description=_optional_text,
    )


def diffs() -> st.SearchStrategy:
    """Strategi Diff valid dengan seluruh field list acak."""
    lst = st.lists(_text, max_size=8)
    return st.builds(
        Diff,
        text_added=lst,
        text_removed=lst,
        links_added=lst,
        links_removed=lst,
        sections_added=lst,
        sections_removed=lst,
        images_added=lst,
        images_removed=lst,
        images_changed=lst,
        title_changed=_optional_pair,
        meta_changed=_optional_pair,
    )


def alerts() -> st.SearchStrategy:
    """Strategi Alert valid, termasuk url/detail None (opsional)."""
    return st.builds(
        Alert,
        id=_text,
        website_id=_text,
        url=_optional_text,
        alert_type=_text,
        severity=st.sampled_from(["critical", "warning", "info"]),
        title=_text,
        triggered_at=_datetimes,
        detail=_optional_text,
        status=st.sampled_from(["unread", "read", "resolved"]),
    )


def change_summaries() -> st.SearchStrategy:
    counts = st.integers(min_value=0, max_value=10_000)
    return st.builds(
        ChangeSummary,
        text_added=counts,
        text_removed=counts,
        links_added=counts,
        links_removed=counts,
        images_changed=counts,
    )


def change_events() -> st.SearchStrategy:
    return st.builds(
        ChangeEvent,
        id=_text,
        website_id=_text,
        url=_url,
        detected_at=_datetimes,
        diff=diffs(),
        summary=change_summaries(),
    )


# --------------------------------------------------------------------------- #
# Property 14: Round-trip serialisasi Snapshot
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 14: Round-trip serialisasi Snapshot
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(snapshots())
def test_snapshot_roundtrip(snapshot: Snapshot):
    """Validates: Requirements 5.2

    Menyerialkan lalu mendeserialkan Snapshot menghasilkan Snapshot setara,
    termasuk mempertahankan link & Image_Hash kosong sebagai kosong.
    """
    restored = deserialize_snapshot(serialize_snapshot(snapshot))
    assert restored == snapshot
    # Koleksi kosong tetap kosong (bukan None) setelah round-trip.
    assert restored.links == snapshot.links
    assert restored.image_hashes == snapshot.image_hashes


# Feature: website-monitoring, Property 14: Round-trip serialisasi Snapshot
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(snapshots(min_links=1, min_hashes=1))
def test_snapshot_roundtrip_nonempty_collections(snapshot: Snapshot):
    """Validates: Requirements 5.2

    Round-trip pada Snapshot dengan link & Image_Hash non-kosong tetap setara.
    """
    restored = deserialize_snapshot(serialize_snapshot(snapshot))
    assert restored == snapshot


# --------------------------------------------------------------------------- #
# Round-trip Diff & ChangeEvent (cakupan task 2.2)
# --------------------------------------------------------------------------- #
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(diffs())
def test_diff_roundtrip(diff: Diff):
    """Validates: Requirements 5.2"""
    assert deserialize_diff(serialize_diff(diff)) == diff


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(change_events())
def test_change_event_roundtrip(event: ChangeEvent):
    """Validates: Requirements 5.2"""
    assert deserialize_change_event(serialize_change_event(event)) == event


# --------------------------------------------------------------------------- #
# Round-trip Alert (ContentMonitor Tahap 3)
# --------------------------------------------------------------------------- #
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(alerts())
def test_alert_roundtrip(alert: Alert):
    """Round-trip serialisasi Alert (ContentMonitor Tahap 3)."""
    assert deserialize_alert(serialize_alert(alert)) == alert


# --------------------------------------------------------------------------- #
# Unit test contoh spesifik
# --------------------------------------------------------------------------- #
def test_snapshot_empty_collections_roundtrip():
    snap = Snapshot(
        url="https://example.com/",
        website_id="w1",
        normalized_text="hello",
        content_hash="hash",
        checked_at=datetime(2020, 1, 1, 12, 0, 0),
    )
    restored = deserialize_snapshot(serialize_snapshot(snap))
    assert restored == snap
    assert restored.links == []
    assert restored.image_hashes == {}


def test_snapshot_datetime_precision_preserved():
    snap = Snapshot(
        url="https://example.com/",
        website_id="w1",
        normalized_text="x",
        content_hash="h",
        checked_at=datetime(2021, 6, 15, 8, 30, 45, 123456),
    )
    restored = deserialize_snapshot(serialize_snapshot(snap))
    assert restored.checked_at == datetime(2021, 6, 15, 8, 30, 45, 123456)


def test_snapshot_from_dict_missing_title_meta_defaults_to_none():
    """Payload lama (sebelum Tahap 3) tanpa title/meta_description tetap
    dapat dideserialkan; keduanya default ke None."""
    old_payload = (
        '{"url": "https://example.com/", "website_id": "w1", '
        '"normalized_text": "halo", "content_hash": "h", '
        '"checked_at": "2020-01-01T00:00:00", "links": [], "image_hashes": {}}'
    )
    snap = deserialize_snapshot(old_payload)
    assert snap.title is None
    assert snap.meta_description is None


def test_diff_from_dict_missing_title_meta_changed_defaults_to_none():
    """Payload Diff lama (sebelum Tahap 3) tanpa title_changed/meta_changed
    tetap dapat dideserialkan; keduanya default ke None."""
    old_payload = (
        '{"text_added": ["a"], "text_removed": [], "links_added": [], '
        '"links_removed": [], "sections_added": [], "sections_removed": [], '
        '"images_added": [], "images_removed": [], "images_changed": []}'
    )
    diff = deserialize_diff(old_payload)
    assert diff.title_changed is None
    assert diff.meta_changed is None
    assert diff.text_added == ["a"]


def test_change_event_nested_diff_roundtrip():
    event = ChangeEvent(
        id="evt-1",
        website_id="w1",
        url="https://example.com/page",
        detected_at=datetime(2022, 3, 3, 10, 0, 0),
        diff=Diff(text_added=["new"], links_removed=["https://example.com/old"]),
        summary=ChangeSummary(1, 0, 0, 1, 0),
    )
    restored = deserialize_change_event(serialize_change_event(event))
    assert restored == event
    assert restored.diff.text_added == ["new"]
    assert restored.summary.links_removed == 1
