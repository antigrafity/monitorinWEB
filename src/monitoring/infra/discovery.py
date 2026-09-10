"""Page_Discovery — penemuan URL halaman dalam sebuah domain (task 10).

Mengimplementasikan Requirement 2: cukup dengan sebuah domain, sistem menemukan
seluruh halaman di dalamnya melalui sitemap dan/atau penelusuran link internal.

Alur ``discover_pages`` (async, memakai :class:`~monitoring.infra.fetcher.Fetcher`):

1. **Sitemap** (Req 2.1): coba ambil ``https://<domain>/sitemap.xml`` (timeout
   30s ditangani Fetcher). Bila berhasil dan ``parse_sitemap`` mengembalikan
   daftar URL yang valid, kumpulkan URL yang host-nya sama persis, normalisasi,
   dedup, dan batasi ≤ ``MAX_URLS_PER_WEBSITE``. Bila daftar host-sama tidak
   kosong, itulah hasilnya.
2. **Fallback crawl** (Req 2.2, 2.3, 2.5): bila sitemap gagal diambil atau
   tidak berisi daftar URL host-sama yang valid, telusuri link internal mulai
   dari halaman utama (BFS) sampai kedalaman ≤ ``MAX_CRAWL_DEPTH`` (5 tingkat
   dari homepage), hanya pada host yang sama persis. Halaman produk/kategori
   yang ditemukan lewat link internal ikut tersertakan secara alami.
3. **Dedup & batas jumlah** (Req 2.4, 2.6): URL dikanonikalisasi via
   ``normalize_url`` + ``dedup_urls`` dan jumlahnya dibatasi ≤
   ``MAX_URLS_PER_WEBSITE``; setelah batas tercapai penemuan dihentikan.
4. **Homepage tak dapat diakses** (Req 2.7): bila halaman utama gagal diambil
   saat crawl, penemuan dihentikan dengan ``failed=True`` dan
   ``failure_reason``. ``discover_pages`` adalah komputasi murni yang hanya
   mengembalikan hasil (tidak mem-persist), sehingga daftar URL yang tersimpan
   sebelumnya tidak diubah — pemanggil yang memutuskan untuk tidak menimpanya.
   Dalam kasus ini ``urls`` dikembalikan kosong.

Logika pembatas BFS (kedalaman & jumlah) diekstrak ke fungsi murni
``crawl_link_graph`` sehingga dapat diuji langsung dengan property-based test
(Property 6 & 7) memakai graf link statis dan parameter batas kecil.

Kompatibel Python 3.9 melalui ``from __future__ import annotations``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from monitoring.config import MAX_CRAWL_DEPTH, MAX_URLS_PER_WEBSITE, SITEMAP_PATHS
from monitoring.domain.models import WebsiteConfig
from monitoring.domain.normalizer import extract_links
from monitoring.domain.urls import (
    dedup_urls,
    is_same_host,
    normalize_url,
    parse_sitemap,
)
from monitoring.infra.fetcher import Fetcher


@dataclass(frozen=True)
class DiscoveryResult:
    """Hasil penemuan halaman untuk sebuah Monitored_Website.

    Attributes:
        urls: Daftar URL halaman ternormalisasi & unik yang ditemukan. Kosong
            bila penemuan gagal (mis. homepage tak dapat diakses, Req 2.7).
        failed: ``True`` bila penemuan tidak dapat diselesaikan (homepage tak
            dapat diakses saat crawl); selain itu ``False``.
        failure_reason: Penjelasan singkat penyebab kegagalan bila ``failed``,
            atau ``None`` bila berhasil.
    """

    urls: List[str] = field(default_factory=list)
    failed: bool = False
    failure_reason: Optional[str] = None
    # True bila SELURUH kandidat path sitemap gagal DIAMBIL (fetch tidak ok)
    # sehingga penemuan jatuh ke fallback crawl (ContentMonitor Tahap 3,
    # sumber Alert #7). Berbeda dari sitemap yang berhasil diambil tetapi
    # kosong/tidak valid — kasus itu TIDAK dianggap "tidak dapat diakses".
    sitemap_unreachable: bool = False


def crawl_link_graph(
    start_url: str,
    link_graph: Dict[str, List[str]],
    base_host: str,
    max_depth: int = MAX_CRAWL_DEPTH,
    max_urls: int = MAX_URLS_PER_WEBSITE,
) -> List[str]:
    """Telusuri graf link internal secara BFS dengan batas kedalaman & jumlah.

    Fungsi murni (tanpa I/O) yang memusatkan logika pembatas penelusuran agar
    dapat diuji langsung (Property 6 & 7):

    - **Batas kedalaman** (Req 2.2): homepage berada pada kedalaman 0. Sebuah
      simpul hanya diperluas (link-nya diikuti) bila kedalamannya ``<
      max_depth``, sehingga URL yang disertakan tidak pernah berada lebih dalam
      dari ``max_depth`` tingkat dari homepage.
    - **Batas host** (Req 2.3): hanya URL yang host-nya sama persis dengan
      ``base_host`` yang disertakan; subdomain berbeda & domain eksternal
      diabaikan.
    - **Dedup** (Req 2.4): URL dikanonikalisasi via ``normalize_url``; setiap
      URL unik muncul sekali, dalam urutan penemuan BFS.
    - **Batas jumlah** (Req 2.6): pengumpulan berhenti begitu jumlah URL
      mencapai ``max_urls``.

    Args:
        start_url: URL homepage tempat penelusuran dimulai.
        link_graph: Peta ``url_ternormalisasi -> daftar URL keluar``. Kunci
            diasumsikan sudah dalam bentuk ternormalisasi (``normalize_url``).
        base_host: Host domain Monitored_Website (untuk penyaringan host-sama).
        max_depth: Kedalaman maksimum dari homepage (bawaan ``MAX_CRAWL_DEPTH``).
        max_urls: Jumlah URL maksimum yang dikumpulkan (bawaan
            ``MAX_URLS_PER_WEBSITE``).

    Returns:
        Daftar URL ternormalisasi & unik dalam urutan penemuan BFS, dengan
        panjang ≤ ``max_urls`` dan tanpa URL yang lebih dalam dari ``max_depth``.
    """
    start = normalize_url(start_url)
    discovered: List[str] = []
    seen = set()

    if max_urls <= 0:
        return discovered

    # Homepage selalu disertakan sebagai kedalaman 0 (bila host-nya sesuai).
    if not is_same_host(start, base_host):
        return discovered
    seen.add(start)
    discovered.append(start)

    queue = deque([(start, 0)])
    while queue:
        if len(discovered) >= max_urls:
            break
        url, depth = queue.popleft()
        # Jangan perluas simpul yang sudah berada pada kedalaman maksimum.
        if depth >= max_depth:
            continue
        for link in link_graph.get(url, []):
            candidate = normalize_url(link)
            if not is_same_host(candidate, base_host):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            discovered.append(candidate)
            if len(discovered) >= max_urls:
                break
            queue.append((candidate, depth + 1))

    return discovered


async def _try_sitemap(
    website: WebsiteConfig, fetcher: Fetcher, base_host: str
) -> "tuple[List[str], bool]":
    """Coba kumpulkan URL dari sitemap domain (Req 2.1).

    Mencoba setiap kandidat path sitemap. Untuk kandidat pertama yang berhasil
    diambil dan berisi daftar URL yang valid (``parse_sitemap`` != ``None``),
    kumpulkan URL host-sama, normalisasi, dedup, dan batasi ≤
    ``MAX_URLS_PER_WEBSITE``.

    Returns:
        Pasangan ``(urls, unreachable)``. ``urls`` mungkin kosong bila sitemap
        tidak menyediakan URL host-sama. ``unreachable`` bernilai ``True``
        hanya bila SELURUH kandidat gagal DIAMBIL (fetch tidak ok) — bukan
        bila sitemap berhasil diambil namun kosong/tidak valid (Tahap 3,
        sumber Alert #7).
    """
    any_fetch_ok = False
    for path in SITEMAP_PATHS:
        sitemap_url = "https://" + website.domain + path
        result = await fetcher.fetch_page(sitemap_url)
        if not result.ok or not result.html:
            continue
        any_fetch_ok = True

        parsed = parse_sitemap(result.html)
        if parsed is None:
            # Bukan format sitemap valid: coba kandidat berikutnya (bila ada).
            continue

        collected: List[str] = []
        for raw in parsed:
            normalized = normalize_url(raw)
            if is_same_host(normalized, base_host):
                collected.append(normalized)
        collected = dedup_urls(collected)
        if collected:
            return collected[:MAX_URLS_PER_WEBSITE], False
    return [], not any_fetch_ok


async def _crawl_from_homepage(
    homepage: str, fetcher: Fetcher, base_host: str
) -> DiscoveryResult:
    """Telusuri link internal mulai dari homepage (Req 2.2, 2.3, 2.5, 2.6, 2.7).

    BFS live: mengambil homepage lebih dulu; bila gagal diakses, hentikan
    penemuan dengan ``failed=True`` tanpa menghasilkan URL (Req 2.7). Selain
    itu, telusuri link internal sampai kedalaman ≤ ``MAX_CRAWL_DEPTH`` pada host
    yang sama persis, dedup, dan batasi ≤ ``MAX_URLS_PER_WEBSITE``.
    """
    home_result = await fetcher.fetch_page(homepage)
    if not home_result.ok:
        return DiscoveryResult(
            urls=[],
            failed=True,
            failure_reason=(
                "Halaman utama tidak dapat diakses saat penelusuran link "
                f"internal: {home_result.failure_reason or 'penyebab tidak diketahui'}"
            ),
        )

    start = normalize_url(homepage)
    discovered: List[str] = [start]
    seen = {start}
    # Simpan HTML halaman yang sudah diambil agar tidak diambil ulang.
    htmls: Dict[str, Optional[str]] = {start: home_result.html}

    queue = deque([(start, 0)])
    while queue:
        if len(discovered) >= MAX_URLS_PER_WEBSITE:
            break
        url, depth = queue.popleft()
        if depth >= MAX_CRAWL_DEPTH:
            continue

        html = htmls.pop(url, None)
        if html is None:
            # Ambil halaman untuk memperoleh link-nya. Kegagalan mengambil
            # halaman anak TIDAK menghentikan penemuan (hanya homepage yang
            # menghentikan, Req 2.7); halaman gagal cukup tak menyumbang link.
            child = await fetcher.fetch_page(url)
            html = child.html if child.ok else ""

        stop = False
        for link in extract_links(html or "", url):
            candidate = normalize_url(link)
            if not is_same_host(candidate, base_host):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            discovered.append(candidate)
            if len(discovered) >= MAX_URLS_PER_WEBSITE:
                stop = True
                break
            queue.append((candidate, depth + 1))
        if stop:
            break

    return DiscoveryResult(
        urls=discovered[:MAX_URLS_PER_WEBSITE], failed=False, failure_reason=None
    )


async def discover_pages(website: WebsiteConfig, fetcher: Fetcher) -> DiscoveryResult:
    """Temukan daftar URL halaman untuk sebuah Monitored_Website (Req 2.1–2.7).

    Pertama mencoba sitemap; bila tidak menghasilkan URL host-sama, jatuh ke
    penelusuran link internal dari homepage. Lihat dokumentasi modul untuk
    rincian alur dan penanganan kegagalan homepage.

    Args:
        website: Konfigurasi Monitored_Website (domain dipakai untuk menyusun
            URL sitemap & homepage serta sebagai host penyaring).
        fetcher: Fetcher untuk mengambil sitemap dan halaman.

    Returns:
        :class:`DiscoveryResult` berisi URL yang ditemukan atau indikasi gagal.
    """
    base_host = website.domain

    sitemap_urls, sitemap_unreachable = await _try_sitemap(website, fetcher, base_host)
    if sitemap_urls:
        return DiscoveryResult(
            urls=sitemap_urls,
            failed=False,
            failure_reason=None,
            sitemap_unreachable=False,
        )

    homepage = normalize_url("https://" + website.domain + "/")
    result = await _crawl_from_homepage(homepage, fetcher, base_host)
    if sitemap_unreachable:
        result = DiscoveryResult(
            urls=result.urls,
            failed=result.failed,
            failure_reason=result.failure_reason,
            sitemap_unreachable=True,
        )
    return result
