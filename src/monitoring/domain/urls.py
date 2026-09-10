"""Util Page_Discovery murni — kanonikalisasi & penyaringan URL (task 7.1).

Kumpulan fungsi murni (tanpa I/O) yang menopang penemuan halaman
(Page_Discovery) sesuai Requirement 2:

- ``normalize_url(url)`` mengkanonikalisasi URL agar deduplikasi andal:
  skema & host di-lowercase, fragment dihapus, trailing slash dinormalisasi.
- ``is_same_host(candidate, base_host)`` membatasi penemuan hanya pada host
  yang sama persis; subdomain berbeda maupun domain eksternal ditolak
  (Req 2.3). Perbandingan host bersifat case-insensitive.
- ``dedup_urls(urls)`` menghilangkan URL duplikat sambil mempertahankan urutan
  kemunculan pertama; operasi bersifat idempoten (Req 2.4).
- ``parse_sitemap(xml_bytes)`` mengurai sitemap XML dan mengembalikan daftar
  URL ``<loc>``; mengembalikan ``None`` bila konten bukan format sitemap yang
  valid.

Modul ini murni sehingga menjadi target property-based testing (Property 4 & 5).
Kompatibel Python 3.9 melalui ``from __future__ import annotations``.

Catatan keamanan: ``parse_sitemap`` memakai ``xml.etree.ElementTree`` dari
pustaka standar. ``defusedxml`` bukan dependensi terdaftar, sehingga di sini
kita mengandalkan ElementTree yang secara bawaan tidak mengekspansi entitas
eksternal dan akan menaikkan galat penguraian pada masukan yang tidak valid;
setiap galat penguraian ditangani dengan mengembalikan ``None`` (bukan
melempar exception). Sitemap diasumsikan berasal dari situs yang dipantau.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional, Union
from urllib.parse import urlsplit, urlunsplit


def _host_of(value: str) -> str:
    """Ekstrak hostname (lowercase) dari sebuah URL atau string host mentah.

    Menerima URL lengkap (``https://example.com/path``) maupun string host
    mentah yang mungkin menyertakan port atau path (``example.com:8080/x``).
    Mengembalikan string kosong bila host tidak dapat ditentukan.
    """
    text = (value or "").strip()
    if not text:
        return ""
    # URL dengan skema eksplisit: parse langsung.
    if "://" in text:
        return (urlsplit(text).hostname or "").lower()
    # String host mentah: bungkus sebagai netloc agar port/path terpisah.
    return (urlsplit("//" + text).hostname or "").lower()


def normalize_url(url: str) -> str:
    """Kanonikalisasi URL untuk deduplikasi yang andal (Req 2.4).

    Langkah kanonikalisasi bersifat deterministik:

    - Skema dan host (netloc) di-lowercase.
    - Fragment (``#...``) dihapus sepenuhnya.
    - Trailing slash dinormalisasi: path kosong atau ``/`` menjadi ``/``,
      sedangkan path lain kehilangan trailing slash-nya (``/about/`` -> ``/about``).
    - Query string dipertahankan apa adanya (dapat bersifat case-sensitive).

    Args:
        url: URL mentah (idealnya sudah absolut).

    Returns:
        URL ternormalisasi. Untuk masukan identik, keluaran selalu identik.
    """
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    path = parts.path
    if path in ("", "/"):
        path = "/"
    else:
        # Buang trailing slash namun sisakan minimal "/" untuk root.
        path = path.rstrip("/") or "/"

    # Elemen fragment (argumen kelima) dikosongkan untuk menghapus fragment.
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def is_same_host(candidate: str, base_host: str) -> bool:
    """Tentukan apakah ``candidate`` berada pada host yang sama persis (Req 2.3).

    Membandingkan host secara case-insensitive dan menuntut kecocokan penuh.
    Subdomain yang berbeda (mis. ``blog.example.com`` vs ``example.com``) maupun
    domain eksternal ditolak. URL relatif atau tanpa host mengembalikan
    ``False``.

    Args:
        candidate: URL kandidat yang akan diperiksa.
        base_host: Host domain Monitored_Website (boleh berupa URL lengkap atau
            string host mentah).

    Returns:
        ``True`` jika, dan hanya jika, host kandidat sama persis dengan
        ``base_host`` (case-insensitive); selain itu ``False``.
    """
    candidate_host = _host_of(candidate)
    base = _host_of(base_host)
    if not candidate_host or not base:
        return False
    return candidate_host == base


def dedup_urls(urls: List[str]) -> List[str]:
    """Hilangkan URL duplikat, pertahankan urutan kemunculan pertama (Req 2.4).

    Setiap URL unik muncul tepat satu kali dalam urutan kemunculan pertamanya.
    Operasi bersifat idempoten: ``dedup_urls(dedup_urls(x)) == dedup_urls(x)``.

    Args:
        urls: Daftar URL, mungkin mengandung duplikat.

    Returns:
        Daftar baru tanpa duplikat dengan urutan kemunculan pertama dipertahankan.
    """
    seen = set()
    result: List[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _local_name(tag: str) -> str:
    """Ambil nama lokal elemen XML tanpa prefiks namespace.

    ElementTree merepresentasikan tag ber-namespace sebagai ``{uri}nama``;
    fungsi ini mengembalikan bagian ``nama`` saja.
    """
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def parse_sitemap(xml_bytes: Union[bytes, str]) -> Optional[List[str]]:
    """Urai sitemap XML dan kembalikan daftar URL ``<loc>`` (Req 2.1).

    Mendukung dokumen ``<urlset>`` (sitemap biasa) maupun ``<sitemapindex>``
    (indeks sitemap). Elemen ``<loc>`` dikumpulkan dari mana pun letaknya dalam
    pohon dan nilai teksnya di-trim; entri kosong diabaikan.

    Args:
        xml_bytes: Konten sitemap sebagai ``bytes`` atau ``str``.

    Returns:
        Daftar URL dari elemen ``<loc>`` (mungkin kosong bila sitemap valid
        namun tak berisi ``<loc>``), atau ``None`` bila konten kosong, gagal
        diurai, atau bukan format sitemap yang valid (root bukan ``urlset``
        maupun ``sitemapindex``).
    """
    if not xml_bytes:
        return None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    except Exception:
        # Setiap galat penguraian tak terduga tetap diperlakukan sebagai
        # "bukan sitemap valid" alih-alih melempar exception (Req 2.1).
        return None

    if _local_name(root.tag) not in ("urlset", "sitemapindex"):
        return None

    locs: List[str] = []
    for element in root.iter():
        if _local_name(element.tag) == "loc":
            text = (element.text or "").strip()
            if text:
                locs.append(text)
    return locs
