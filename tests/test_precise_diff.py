"""Unit test presisi diff teks & retensi Snapshot end-to-end (Req 6.2, 5.4, 11.4).

Membuktikan dua hal:

1. Karena ``Snapshot.normalized_text`` disimpan sebagai blok-blok yang
   digabung newline (``"\\n".join(extract_text_blocks(html))``), perubahan pada
   SATU blok menghasilkan diff yang hanya memuat blok tersebut — bukan seluruh
   teks halaman (Req 6.2).
2. CheckOrchestrator memangkas Snapshot lama setelah menyimpan yang baru
   sehingga hanya beberapa Snapshot terbaru per halaman yang tersimpan,
   sementara riwayat Change_Event tetap utuh (Req 5.4, 11.4).
"""

from datetime import datetime, timedelta
from typing import List, Optional

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.config import SNAPSHOT_RETENTION_PER_URL
from monitoring.domain.detector import detect_changes
from monitoring.domain.hashing import content_hash
from monitoring.domain.models import Snapshot, WebsiteConfig
from monitoring.domain.normalizer import extract_text_blocks, split_sections
from monitoring.infra.discovery import DiscoveryResult
from monitoring.infra.fetcher import FetchResult, ImageFetchResult
from monitoring.infra.repository import Repository

URL = "https://example.com/"

# Halaman dengan banyak blok; hanya satu paragraf yang berubah antar versi.
_PAGE_TEMPLATE = (
    "<html><body>"
    "<h1>Judul Halaman</h1>"
    "<p>Paragraf pertama yang tidak berubah.</p>"
    "<p>{middle}</p>"
    "<p>Paragraf ketiga yang tidak berubah.</p>"
    "<ul><li>Item satu</li><li>Item dua</li></ul>"
    "</body></html>"
)

PAGE_V1 = _PAGE_TEMPLATE.format(middle="Harga tiket adalah 100 ribu rupiah.")
PAGE_V2 = _PAGE_TEMPLATE.format(middle="Harga tiket adalah 150 ribu rupiah.")


def _website() -> WebsiteConfig:
    return WebsiteConfig(
        id="web-1",
        domain="example.com",
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _snapshot_from_html(html: str, checked_at: datetime) -> Snapshot:
    """Bangun Snapshot seperti yang dilakukan CheckOrchestrator (blok + newline)."""
    text = "\n".join(extract_text_blocks(html))
    return Snapshot(
        url=URL,
        website_id="web-1",
        normalized_text=text,
        content_hash=content_hash(text),
        checked_at=checked_at,
        sections=[s.as_block() for s in split_sections(html)],
    )


# --------------------------------------------------------------------------- #
# Presisi diff teks (Req 6.2)
# --------------------------------------------------------------------------- #


def test_single_block_change_yields_only_that_block():
    """Satu paragraf berubah -> diff hanya memuat blok itu, bukan seluruh halaman."""
    base = datetime(2024, 1, 1, 12, 0, 0)
    previous = _snapshot_from_html(PAGE_V1, base)
    current = _snapshot_from_html(PAGE_V2, base + timedelta(hours=1))

    result = detect_changes(
        previous,
        current,
        previous_sections=previous.sections,
        current_sections=current.sections,
    )

    assert result.changed is True
    diff = result.diff
    assert diff is not None
    assert diff.text_added == ["Harga tiket adalah 150 ribu rupiah."]
    assert diff.text_removed == ["Harga tiket adalah 100 ribu rupiah."]
    # Blok lain TIDAK muncul di diff.
    assert not any("Judul Halaman" in line for line in diff.text_added)
    assert not any("Item satu" in line for line in diff.text_removed)


def test_diff_is_small_relative_to_page_size():
    """Diff jauh lebih kecil daripada jumlah blok halaman (bukan seluruh teks)."""
    base = datetime(2024, 1, 1, 12, 0, 0)
    previous = _snapshot_from_html(PAGE_V1, base)
    current = _snapshot_from_html(PAGE_V2, base + timedelta(hours=1))
    total_blocks = len(extract_text_blocks(PAGE_V2))

    result = detect_changes(previous, current)

    assert total_blocks >= 6
    assert len(result.diff.text_added) == 1
    assert len(result.diff.text_removed) == 1
    assert result.change_event.summary.text_added == 1
    assert result.change_event.summary.text_removed == 1


def test_added_paragraph_reports_only_the_new_block():
    """Menambah satu paragraf -> hanya blok baru pada text_added, tanpa text_removed."""
    base = datetime(2024, 1, 1, 12, 0, 0)
    with_extra = PAGE_V1.replace(
        "</ul>", "</ul><p>Paragraf tambahan.</p>"
    )
    previous = _snapshot_from_html(PAGE_V1, base)
    current = _snapshot_from_html(with_extra, base + timedelta(hours=1))

    result = detect_changes(previous, current)

    assert result.diff.text_added == ["Paragraf tambahan."]
    assert result.diff.text_removed == []


def test_snapshot_text_is_block_joined_multiline():
    """Teks Snapshot berisi satu baris per blok sehingga diff baris presisi."""
    snap = _snapshot_from_html(PAGE_V1, datetime(2024, 1, 1, 12, 0, 0))
    lines = snap.normalized_text.splitlines()
    assert lines[0] == "Judul Halaman"
    assert len(lines) == len(extract_text_blocks(PAGE_V1))
    assert len(lines) > 1


# --------------------------------------------------------------------------- #
# Integrasi orchestrator: presisi diff + retensi Snapshot (Req 5.4, 6.2, 11.4)
# --------------------------------------------------------------------------- #


class _StubFetcher:
    """Fetcher tiruan yang mengembalikan HTML dari daftar ``pages`` per panggilan."""

    def __init__(self, htmls: List[str]) -> None:
        self._htmls = list(htmls)
        self._calls = 0

    async def fetch_page(self, url: str) -> FetchResult:
        html = self._htmls[min(self._calls, len(self._htmls) - 1)]
        self._calls += 1
        return FetchResult(
            url=url, ok=True, status_code=200, html=html, failure_reason=None
        )

    async def fetch_image(self, url: str) -> ImageFetchResult:
        return ImageFetchResult(
            url=url, ok=False, content=None, failure_reason="tak dipakai"
        )


class _StubNotifier:
    def __init__(self) -> None:
        self.calls: List = []

    async def notify(self, event, website) -> bool:
        self.calls.append((event, website))
        return True


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


async def _count_snapshots(repo: Repository, url: str) -> int:
    conn = repo._require_conn()
    async with conn.execute(
        "SELECT COUNT(*) FROM snapshot WHERE url = ?", (url,)
    ) as cur:
        (count,) = await cur.fetchone()
    return int(count)


def _patch_discovery(monkeypatch, urls: Optional[List[str]] = None) -> None:
    async def fake_discover(website, fetcher):
        return DiscoveryResult(
            urls=urls if urls is not None else [URL],
            failed=False,
            failure_reason=None,
        )

    monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)


async def test_orchestrator_change_event_holds_precise_diff(tmp_path, monkeypatch):
    """Diff pada Change_Event yang tersimpan hanya memuat blok yang berubah (Req 6.2)."""
    _patch_discovery(monkeypatch)
    fetcher = _StubFetcher([PAGE_V1, PAGE_V2])
    notifier = _StubNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1, 12, 0, 0))
        )
        website = _website()

        first = await orch.check_website(website)
        assert first.changes_detected == 0  # baseline (Req 6.7)

        second = await orch.check_website(website)
        assert second.changes_detected == 1

        events = await repo.list_change_events("web-1")
        assert len(events) == 1
        diff = events[0].diff
        assert diff.text_added == ["Harga tiket adalah 150 ribu rupiah."]
        assert diff.text_removed == ["Harga tiket adalah 100 ribu rupiah."]


async def test_orchestrator_prunes_snapshots_but_keeps_events(tmp_path, monkeypatch):
    """Pemeriksaan berulang tidak menumbuhkan Snapshot tanpa batas (Req 5.4, 11.4)."""
    _patch_discovery(monkeypatch)
    total_checks = SNAPSHOT_RETENTION_PER_URL + 4
    # Setiap pemeriksaan menghasilkan teks berbeda -> selalu ada Change_Event.
    htmls = [
        _PAGE_TEMPLATE.format(middle=f"Harga tiket adalah {i} ribu rupiah.")
        for i in range(total_checks)
    ]
    fetcher = _StubFetcher(htmls)
    notifier = _StubNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1, 12, 0, 0))
        )
        website = _website()

        for _ in range(total_checks):
            await orch.check_website(website)

        # Snapshot dibatasi oleh kebijakan retensi.
        assert await _count_snapshots(repo, URL) == SNAPSHOT_RETENTION_PER_URL

        # Baseline terbaru tetap tersedia untuk perbandingan berikutnya.
        latest = await repo.get_latest_snapshot(URL)
        assert latest is not None
        assert latest.normalized_text.splitlines()[2] == (
            f"Harga tiket adalah {total_checks - 1} ribu rupiah."
        )

        # Riwayat Change_Event utuh: satu per pemeriksaan setelah baseline.
        events = await repo.list_change_events("web-1")
        assert len(events) == total_checks - 1
