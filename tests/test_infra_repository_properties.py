"""Property-based tests untuk operasi riwayat Repository (task 8.3).

Menguji dua properti desain:

- Property 24: Pengurutan riwayat Change_Event (Req 10.4).
- Property 25: Change_Event historis dipertahankan saat snapshot baru
  disimpan (Req 11.4).

Karena Repository bersifat async sementara Hypothesis menjalankan setiap
contoh secara sinkron, tiap contoh menjalankan skenario async lewat
``asyncio.run`` di atas basis data SQLite in-memory yang segar. Ini menjaga
isolasi antar-contoh dan menghindari benturan dengan pytest-asyncio.

Minimal 100 iterasi per properti (``@settings(max_examples=100)``).
"""

import asyncio
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    Snapshot,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository

WEBSITE_ID = "web-1"

# Batasi ke rentang tahun yang nyaman & datetime naive agar round-trip
# ISO string stabil dan pengurutan leksikografis == kronologis.
_detected_at = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2035, 12, 31, 23, 59, 59),
)

# Daftar detected_at untuk sekumpulan Change_Event milik satu website.
_detected_at_lists = st.lists(_detected_at, min_size=1, max_size=25)


def _website() -> WebsiteConfig:
    return WebsiteConfig(
        id=WEBSITE_ID,
        domain="example.com",
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _events_from(detected_ats) -> list:
    """Bangun daftar Change_Event dengan id unik dari daftar detected_at."""
    events = []
    for i, dt in enumerate(detected_ats):
        events.append(
            ChangeEvent(
                id=f"evt-{i}",
                website_id=WEBSITE_ID,
                url=f"https://example.com/page-{i % 5}",
                detected_at=dt,
                diff=Diff(text_added=[f"baris-{i}"]),
                summary=ChangeSummary(
                    text_added=1,
                    text_removed=0,
                    links_added=0,
                    links_removed=0,
                    images_changed=0,
                ),
            )
        )
    return events


# Feature: website-monitoring, Property 24: Pengurutan riwayat Change_Event
# Validates: Requirements 10.4
@settings(max_examples=100)
@given(detected_ats=_detected_at_lists)
def test_property_change_events_ordered_desc(detected_ats):
    """Untuk himpunan Change_Event apa pun, list_change_events mengembalikan
    mereka terurut menurun berdasarkan detected_at (terbaru ke terlama)."""
    events = _events_from(detected_ats)

    async def scenario():
        async with Repository(":memory:") as repo:
            await repo.add_website(_website())
            for evt in events:
                assert await repo.save_change_event(evt) is True
            return await repo.list_change_events(WEBSITE_ID)

    stored = asyncio.run(scenario())

    # Semua event dikembalikan.
    assert len(stored) == len(events)
    assert {e.id for e in stored} == {e.id for e in events}

    # Terurut menurun berdasarkan detected_at (non-increasing).
    detected = [e.detected_at for e in stored]
    assert detected == sorted(detected, reverse=True)


# Feature: website-monitoring, Property 25: Change_Event historis dipertahankan saat snapshot baru disimpan
# Validates: Requirements 11.4
@settings(max_examples=100)
@given(detected_ats=_detected_at_lists, snap_hash=st.text(min_size=1, max_size=32))
def test_property_change_events_preserved_when_snapshot_saved(detected_ats, snap_hash):
    """Untuk himpunan Change_Event yang sudah tersimpan, menyimpan Snapshot baru
    tidak menghapus maupun mengubah Change_Event historis."""
    events = _events_from(detected_ats)

    async def scenario():
        async with Repository(":memory:") as repo:
            await repo.add_website(_website())
            for evt in events:
                assert await repo.save_change_event(evt) is True

            before = await repo.list_change_events(WEBSITE_ID)

            # Simpan Snapshot baru untuk website yang sama.
            snap = Snapshot(
                url="https://example.com/page-0",
                website_id=WEBSITE_ID,
                normalized_text="konten baru",
                content_hash=snap_hash,
                checked_at=datetime(2024, 6, 1, 0, 0, 0),
                links=["https://example.com/x"],
                image_hashes={"https://example.com/i.png": "h"},
            )
            assert await repo.save_snapshot(snap) is True

            after = await repo.list_change_events(WEBSITE_ID)
            return before, after

    before, after = asyncio.run(scenario())

    # Tidak ada event yang hilang, ditambah, atau berubah.
    def _key(events):
        return {
            e.id: (
                e.website_id,
                e.url,
                e.detected_at,
                tuple(e.diff.text_added),
                e.summary.text_added,
            )
            for e in events
        }

    assert _key(before) == _key(after)
    assert len(before) == len(events)
