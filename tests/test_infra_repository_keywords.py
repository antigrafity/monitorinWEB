"""Unit tests untuk operasi CRUD Keyword, state, dan prune_change_events (ContentMonitor Tahap 4).

Menguji:
- Penambahan, penampilan, dan penghapusan kata kunci di Repository.
- Penyimpanan dan pembacaan keyword_state (dedup alert per halaman).
- Repository.prune_change_events(older_than_days): hanya menghapus change_event
  yang kedaluwarsa dan tidak menyentuh tabel lain maupun event baru.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from monitoring.domain.models import Alert, ChangeEvent, ChangeSummary, Keyword, Snapshot, WebsiteConfig
from monitoring.domain.serialization import diff_from_dict
from monitoring.infra.repository import Repository


@pytest.fixture
async def repo(tmp_path: Path) -> Repository:
    """Repository berbasis file temporer untuk isolasi pengujian."""
    db_file = tmp_path / "test_keywords.db"
    repository = Repository(str(db_file))
    await repository.connect()
    return repository


async def test_add_and_list_keywords(repo: Repository):
    """Menguji penambahan dan penampilan kata kunci per website."""
    kw1 = await repo.add_keyword(
        id=str(uuid4()),
        website_id="web-1",
        keyword="diskon",
        mode="must_exist",
    )
    kw2 = await repo.add_keyword(
        id=str(uuid4()),
        website_id="web-1",
        keyword="error 500",
        mode="must_not_exist",
    )
    kw3 = await repo.add_keyword(
        id=str(uuid4()),
        website_id="web-2",
        keyword="promo",
        mode="must_exist",
    )

    all_kws = await repo.list_keywords()
    assert len(all_kws) == 3

    web1_kws = await repo.list_keywords("web-1")
    assert len(web1_kws) == 2
    assert {k.keyword for k in web1_kws} == {"diskon", "error 500"}

    web2_kws = await repo.list_keywords("web-2")
    assert len(web2_kws) == 1
    assert web2_kws[0].keyword == "promo"


async def test_remove_keyword(repo: Repository):
    """Menguji penghapusan kata kunci dan state-nya."""
    kw = await repo.add_keyword(
        id="kw-del",
        website_id="web-1",
        keyword="rahasia",
        mode="must_not_exist",
    )
    await repo.set_keyword_state("web-1", "https://example.com", "kw-del", "found")

    assert await repo.get_keyword_state("web-1", "https://example.com", "kw-del") == "found"
    assert len(await repo.list_keywords("web-1")) == 1

    removed = await repo.remove_keyword("kw-del")
    assert removed is True
    assert len(await repo.list_keywords("web-1")) == 0
    assert await repo.get_keyword_state("web-1", "https://example.com", "kw-del") is None


async def test_keyword_state_upsert(repo: Repository):
    """Menguji penyimpan dan pembacaan state kata kunci per halaman."""
    assert await repo.get_keyword_state("web-1", "https://example.com", "kw-1") is None

    await repo.set_keyword_state("web-1", "https://example.com", "kw-1", "missing")
    assert await repo.get_keyword_state("web-1", "https://example.com", "kw-1") == "missing"

    await repo.set_keyword_state("web-1", "https://example.com", "kw-1", "ok")
    assert await repo.get_keyword_state("web-1", "https://example.com", "kw-1") == "ok"


async def test_prune_change_events_safe(repo: Repository):
    """Menguji bahwa prune_change_events HANYA menghapus event lama dan tidak merusak data lain."""
    # 1. Siapkan website, snapshot, alert, dan change_event (lama & baru)
    web = WebsiteConfig(
        id="web-1",
        domain="example.com",
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime.utcnow(),
    )
    await repo.add_website(web)

    now = datetime.utcnow()
    old_time = now - timedelta(days=100)
    new_time = now - timedelta(days=10)

    # Snapshot (untuk memastikan TIDAK terhapus oleh prune)
    snap = Snapshot(
        url="https://example.com",
        website_id="web-1",
        content_hash="hash1",
        normalized_text="text old",
        checked_at=old_time,
    )
    await repo.save_snapshot(snap)

    # Alert lama & baru
    alert_old = Alert(
        id="alert-old",
        website_id="web-1",
        url="https://example.com",
        alert_type="page_unreachable",
        severity="warning",
        title="Old Alert",
        triggered_at=old_time,
    )
    alert_new = Alert(
        id="alert-new",
        website_id="web-1",
        url="https://example.com",
        alert_type="page_unreachable",
        severity="warning",
        title="New Alert",
        triggered_at=new_time,
    )
    await repo.save_alert(alert_old)
    await repo.save_alert(alert_new)

    # ChangeEvent lama & baru
    diff = diff_from_dict({"type": "added", "added": ["hello"], "removed": []})
    summary = ChangeSummary(text_added=1, text_removed=0, links_added=0, links_removed=0, images_changed=0)
    evt_old = ChangeEvent(
        id="evt-old",
        website_id="web-1",
        url="https://example.com",
        detected_at=old_time,
        diff=diff,
        summary=summary,
    )
    evt_new = ChangeEvent(
        id="evt-new",
        website_id="web-1",
        url="https://example.com",
        detected_at=new_time,
        diff=diff,
        summary=summary,
    )
    await repo.save_change_event(evt_old)
    await repo.save_change_event(evt_new)

    # Verifikasi data sebelum prune
    events_before = await repo.list_change_events_between(old_time - timedelta(days=1), now + timedelta(days=1))
    assert len(events_before) == 2

    # 2. Lakukan prune untuk retensi 90 hari (seharusnya hapus yang >= 100 hari lalu)
    deleted_count = await repo.prune_change_events(older_than_days=90)
    assert deleted_count == 1

    # 3. Verifikasi change_event yang tersisa (hanya evt_new)
    events_after = await repo.list_change_events_between(old_time - timedelta(days=1), now + timedelta(days=1))
    assert len(events_after) == 1
    assert events_after[0].id == "evt-new"

    # 4. Verifikasi bahwa tabel lain sama sekali TIDAK tersentuh
    all_webs = await repo.list_websites()
    assert len(all_webs) == 1

    latest_snap = await repo.get_latest_snapshot("https://example.com")
    assert latest_snap is not None

    alerts = await repo.recent_alerts(limit=10)
    assert len(alerts) == 2
