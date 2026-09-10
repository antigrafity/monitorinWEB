"""Unit & integrasi test halaman Content Changes ContentMonitor (Tahap 2).

Mencakup ``GET /changes`` sesuai spesifikasi Tahap 2:

- 4 kartu KPI (Semua/Ditambahkan/Dihapus/Diperbarui) dihitung dari rentang
  tanggal AKTIF memakai ``classify_change``;
- filter tanggal/website/tipe/search bekerja sendiri-sendiri MAUPUN
  dikombinasikan;
- pagination benar ("Showing X to Y of Z changes"), termasuk saat filter tipe
  aktif (pendekatan filter-di-Python pada :func:`_load_changes_context`);
- rentang tanggal tidak valid (format salah / end < start) TIDAK menyebabkan
  error 500, melainkan jatuh ke bawaan dengan peringatan halus;
- empty state membedakan "belum ada perubahan" vs "tidak ada hasil untuk
  filter ini";
- sidebar menandai "Content Changes" sebagai item aktif.

Menggunakan repository palsu (fake) yang meniru kontrak
``list_change_events_filtered``/``count_change_events_filtered``/
``list_change_events_between`` dari :class:`~monitoring.infra.repository.Repository`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    WebsiteConfig,
)
from monitoring.web.app import CHANGES_PAGE_SIZE, create_app


def _website(website_id: str, domain: str, name: Optional[str] = None) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name or domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _event(
    event_id: str,
    website_id: str,
    url: str,
    detected_at: datetime,
    diff: Optional[Diff] = None,
) -> ChangeEvent:
    diff = diff or Diff()
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url=url,
        detected_at=detected_at,
        diff=diff,
        summary=ChangeSummary(
            text_added=len(diff.text_added),
            text_removed=len(diff.text_removed),
            links_added=len(diff.links_added),
            links_removed=len(diff.links_removed),
            images_changed=len(diff.images_changed),
        ),
    )


# Diff yang HANYA berisi penambahan -> classify_change == "added".
def _added_diff() -> Diff:
    return Diff(text_added=["konten baru"])


# Diff yang HANYA berisi penghapusan -> classify_change == "removed".
def _removed_diff() -> Diff:
    return Diff(text_removed=["konten lama"])


# Diff campuran -> classify_change == "updated".
def _updated_diff() -> Diff:
    return Diff(text_added=["baru"], text_removed=["lama"])


class FakeRepository:
    """Repository palsu untuk halaman Content Changes.

    Meniru kontrak filter tanggal/website/URL dari Repository nyata; filter
    tipe TIDAK diimplementasikan di sini (sesuai desain: tipe difilter di
    lapisan presentasi/Python, bukan repository).
    """

    def __init__(
        self,
        websites: Optional[List[WebsiteConfig]] = None,
        events: Optional[List[ChangeEvent]] = None,
    ):
        self._websites = {w.id: w for w in (websites or [])}
        self._events = list(events or [])

    async def list_websites(self):
        return list(self._websites.values())

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    def _matches(self, e: ChangeEvent, start, end, website_id, url_query) -> bool:
        if not (start <= e.detected_at < end):
            return False
        if website_id is not None and e.website_id != website_id:
            return False
        if url_query and url_query.lower() not in e.url.lower():
            return False
        return True

    async def list_change_events_filtered(
        self,
        start,
        end,
        website_id=None,
        url_query=None,
        limit=10,
        offset=0,
    ):
        items = [
            e
            for e in self._events
            if self._matches(e, start, end, website_id, url_query)
        ]
        items = sorted(items, key=lambda e: e.detected_at, reverse=True)
        return items[offset : offset + limit]

    async def count_change_events_filtered(
        self, start, end, website_id=None, url_query=None
    ):
        return len(
            [
                e
                for e in self._events
                if self._matches(e, start, end, website_id, url_query)
            ]
        )

    async def list_change_events_between(self, start, end, website_id=None):
        items = [
            e
            for e in self._events
            if start <= e.detected_at < end
            and (website_id is None or e.website_id == website_id)
        ]
        return sorted(items, key=lambda e: e.detected_at, reverse=True)


class FailingRepository:
    """Repository palsu yang selalu gagal memuat data."""

    async def list_websites(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository) -> TestClient:
    return TestClient(create_app(repository), follow_redirects=False)


# --- KPI --------------------------------------------------------------- #


def test_kpi_counts_per_type_are_correct():
    """4 kartu KPI menghitung jumlah per tipe dengan benar."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(hours=1), _added_diff()),
        _event("e2", "w1", "https://a.com/2", now - timedelta(hours=2), _added_diff()),
        _event("e3", "w1", "https://a.com/3", now - timedelta(hours=3), _removed_diff()),
        _event("e4", "w1", "https://a.com/4", now - timedelta(hours=4), _updated_diff()),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes").text

    assert '<div class="kpi-value" data-kpi-value="total">4</div>' in body
    assert '<div class="kpi-value" data-kpi-value="added">2</div>' in body
    assert '<div class="kpi-value" data-kpi-value="removed">1</div>' in body
    assert '<div class="kpi-value" data-kpi-value="updated">1</div>' in body


def test_kpi_unaffected_by_website_type_search_filters():
    """KPI dihitung dari rentang tanggal aktif, TIDAK terpengaruh filter lain."""
    now = datetime.now()
    websites = [_website("w1", "a.com"), _website("w2", "b.com")]
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(hours=1), _added_diff()),
        _event("e2", "w2", "https://b.com/2", now - timedelta(hours=2), _removed_diff()),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?website=w1&type=added&q=xyz").text
    # KPI tetap menghitung SELURUH event dalam rentang tanggal (2), bukan
    # hanya yang cocok filter website/type/search.
    assert '<div class="kpi-value" data-kpi-value="total">2</div>' in body


# --- Filter tanggal ------------------------------------------------------ #


def test_date_range_filter_restricts_results():
    """Filter start/end membatasi hasil pada rentang yang diberikan."""
    websites = [_website("w1", "a.com")]
    events = [
        _event("e1", "w1", "https://a.com/1", datetime(2024, 6, 5, 10, 0)),
        _event("e2", "w1", "https://a.com/2", datetime(2024, 6, 15, 10, 0)),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?start=2024-06-01&end=2024-06-10").text
    assert "a.com/1" in body
    assert "a.com/2" not in body


def test_invalid_date_range_falls_back_to_default_without_error():
    """Rentang tanggal tidak valid (end < start) -> bawaan, TIDAK error 500."""
    repo = FakeRepository([_website("w1", "a.com")], [])
    resp = _client(repo).get("/changes?start=2024-06-10&end=2024-06-01")
    assert resp.status_code == 200
    assert "tidak valid" in resp.text.lower()


def test_malformed_date_format_falls_back_to_default_without_error():
    """Format tanggal salah -> bawaan, TIDAK error 500."""
    repo = FakeRepository([_website("w1", "a.com")], [])
    resp = _client(repo).get("/changes?start=bukan-tanggal&end=juga-bukan")
    assert resp.status_code == 200
    assert "tidak valid" in resp.text.lower()


# --- Filter website ------------------------------------------------------- #


def test_website_filter_restricts_results():
    """Filter `website` membatasi hasil pada satu Monitored_Website."""
    now = datetime.now()
    websites = [_website("w1", "a.com"), _website("w2", "b.com")]
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(hours=1)),
        _event("e2", "w2", "https://b.com/2", now - timedelta(hours=2)),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?website=w1").text
    assert "a.com/1" in body
    assert "b.com/2" not in body


# --- Filter tipe (di Python, lihat docstring _load_changes_context) ------- #


def test_type_filter_restricts_to_added_only():
    """Filter `type=added` hanya menampilkan perubahan tipe "added"."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(hours=1), _added_diff()),
        _event("e2", "w1", "https://a.com/2", now - timedelta(hours=2), _removed_diff()),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?type=added").text
    assert "a.com/1" in body
    assert "a.com/2" not in body
    assert "Showing 1 to 1 of 1 changes" in body


# --- Filter search --------------------------------------------------------- #


def test_search_matches_url():
    """Search (`q`) mencocokkan URL halaman."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event("e1", "w1", "https://a.com/promo", now - timedelta(hours=1)),
        _event("e2", "w1", "https://a.com/contact", now - timedelta(hours=2)),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?q=promo").text
    assert "a.com/promo" in body
    assert "a.com/contact" not in body


def test_search_matches_description():
    """Search (`q`) juga mencocokkan deskripsi Diff (describe_change)."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event(
            "e1",
            "w1",
            "https://a.com/promo",
            now - timedelta(hours=1),
            Diff(text_added=["diskon spesial"]),
        ),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?q=bagian+teks+baru").text
    assert "a.com/promo" in body


# --- Kombinasi filter ------------------------------------------------------ #


def test_combined_filters_date_website_type_search():
    """Filter tanggal + website + tipe + search dapat dikombinasikan."""
    websites = [_website("w1", "a.com"), _website("w2", "b.com")]
    events = [
        # Cocok semua filter.
        _event(
            "e1",
            "w1",
            "https://a.com/promo",
            datetime(2024, 6, 5, 10, 0),
            _added_diff(),
        ),
        # Website salah.
        _event(
            "e2",
            "w2",
            "https://b.com/promo",
            datetime(2024, 6, 5, 10, 0),
            _added_diff(),
        ),
        # Tipe salah.
        _event(
            "e3",
            "w1",
            "https://a.com/promo2",
            datetime(2024, 6, 5, 10, 0),
            _removed_diff(),
        ),
        # Di luar rentang tanggal.
        _event(
            "e4",
            "w1",
            "https://a.com/promo3",
            datetime(2024, 7, 5, 10, 0),
            _added_diff(),
        ),
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get(
        "/changes?start=2024-06-01&end=2024-06-10&website=w1&type=added&q=promo"
    ).text
    assert "a.com/promo<" in body or "a.com/promo\"" in body or "a.com/promo\n" in body
    assert "Showing 1 to 1 of 1 changes" in body


# --- Pagination -------------------------------------------------------------- #


def test_pagination_shows_range_text_without_type_filter():
    """Pagination benar (jalur cepat SQL, tanpa filter tipe/search)."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event(f"e{i}", "w1", f"https://a.com/{i}", now - timedelta(hours=i))
        for i in range(15)
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?page=1").text
    assert "Showing 1 to {0} of 15 changes".format(CHANGES_PAGE_SIZE) in body

    body2 = _client(repo).get("/changes?page=2").text
    assert "Showing 11 to 15 of 15 changes" in body2


def test_pagination_correct_with_type_filter_active():
    """Pagination tetap benar saat filter tipe aktif (jalur filter-di-Python)."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    # 12 event "added" + 3 event "removed" -> filter type=added harus
    # menghasilkan total 12, terpotong 10/halaman.
    events = [
        _event(
            f"add{i}", "w1", f"https://a.com/add{i}", now - timedelta(hours=i), _added_diff()
        )
        for i in range(12)
    ] + [
        _event(
            f"rem{i}",
            "w1",
            f"https://a.com/rem{i}",
            now - timedelta(hours=i),
            _removed_diff(),
        )
        for i in range(3)
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?type=added&page=1").text
    assert "Showing 1 to 10 of 12 changes" in body

    body2 = _client(repo).get("/changes?type=added&page=2").text
    assert "Showing 11 to 12 of 12 changes" in body2


def test_pagination_out_of_range_page_does_not_error():
    """Halaman di luar rentang dijepit ke halaman valid, TIDAK error."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [
        _event(f"e{i}", "w1", f"https://a.com/{i}", now - timedelta(hours=i))
        for i in range(3)
    ]
    repo = FakeRepository(websites, events)
    resp = _client(repo).get("/changes?page=999")
    assert resp.status_code == 200
    assert "Showing 1 to 3 of 3 changes" in resp.text


# --- Empty state -------------------------------------------------------- #


def test_empty_state_no_changes_ever():
    """Belum ada perubahan sama sekali -> empty state "belum ada perubahan"."""
    repo = FakeRepository([_website("w1", "a.com")], [])
    body = _client(repo).get("/changes").text
    assert "Belum ada perubahan yang terdeteksi" in body


def test_empty_state_no_results_for_filter():
    """Ada perubahan, tetapi tidak ada yang cocok filter -> pesan berbeda."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [_event("e1", "w1", "https://a.com/1", now - timedelta(hours=1))]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes?q=tidakketemu").text
    assert "Tidak ada hasil untuk filter ini" in body
    assert "Belum ada perubahan yang terdeteksi" not in body


# --- Badge & deskripsi ---------------------------------------------------- #


def test_change_type_badge_and_description_rendered():
    """Badge tipe + deskripsi (describe_change) tampil pada baris tabel."""
    now = datetime.now()
    websites = [_website("w1", "a.com", "Situs A")]
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(hours=1), _added_diff())
    ]
    repo = FakeRepository(websites, events)
    body = _client(repo).get("/changes").text
    assert "badge-added" in body
    assert "Menambahkan 1 bagian teks baru" in body
    assert "Situs A" in body
    assert "/events/e1" in body


# --- Sidebar --------------------------------------------------------------- #


def test_sidebar_marks_changes_as_active():
    """Sidebar menandai menu "Content Changes" sebagai aktif pada halaman ini."""
    repo = FakeRepository([], [])
    body = _client(repo).get("/changes").text
    assert 'href="/changes" class="active"' in body
    assert 'aria-current="page"' in body


# --- Kegagalan pemuatan ------------------------------------------------------ #


def test_load_failure_shows_error_indication():
    """Kegagalan pemuatan menampilkan indikasi kesalahan tanpa mutasi data."""
    client = TestClient(
        create_app(FailingRepository()), raise_server_exceptions=False
    )
    resp = client.get("/changes")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi dengan Repository nyata --------------------------------- #


async def test_changes_page_via_real_repository(tmp_path):
    """Integrasi ringan: halaman Content Changes merender data dari Repository nyata."""
    from monitoring.infra.repository import Repository

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website("w1", "nyata.com", "Situs Nyata"))
        event = _event(
            "evt-1", "w1", "https://nyata.com/", datetime.now(), _added_diff()
        )
        assert await repo.save_change_event(event)

        client = TestClient(create_app(repo))
        body = client.get("/changes").text
        assert "nyata.com" in body
        assert "badge-added" in body
