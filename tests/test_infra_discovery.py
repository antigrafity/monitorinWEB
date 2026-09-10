"""Unit test untuk Page_Discovery (``discover_pages``) — task 10.

Menguji perilaku spesifik yang tidak cocok sebagai property (jalur sitemap,
fallback ke crawl, dan kegagalan homepage) memakai Fetcher tiruan berbasis
``async fetch_page`` yang memetakan URL ke :class:`FetchResult`. Tidak ada
jaringan nyata yang tersentuh.

- Sitemap sukses (Req 2.1): sitemap valid -> URL diambil dari sitemap.
- Fallback ke crawl (Req 2.2, 2.5): sitemap gagal -> telusuri link internal
  homepage; halaman produk/kategori lewat link internal ikut tersertakan.
- Homepage tak dapat diakses (Req 2.7): sitemap gagal & homepage gagal ->
  ``failed=True`` dan ``urls`` kosong.
- Penyaringan host-sama (Req 2.3): link eksternal/subdomain diabaikan.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.discovery import discover_pages
from monitoring.infra.fetcher import FetchResult

DOMAIN = "example.com"


def _website() -> WebsiteConfig:
    return WebsiteConfig(
        id="w1",
        domain=DOMAIN,
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


class FakeFetcher:
    """Fetcher tiruan: memetakan URL -> FetchResult; default gagal (ok=False)."""

    def __init__(self, pages: Dict[str, FetchResult]):
        self._pages = pages
        self.requested = []

    async def fetch_page(self, url: str) -> FetchResult:
        self.requested.append(url)
        if url in self._pages:
            return self._pages[url]
        return FetchResult(
            url=url,
            ok=False,
            status_code=404,
            html=None,
            failure_reason="tidak ditemukan",
        )


def _ok(url: str, html: str) -> FetchResult:
    return FetchResult(url=url, ok=True, status_code=200, html=html, failure_reason=None)


def _fail(url: str, reason: str = "gagal") -> FetchResult:
    return FetchResult(
        url=url, ok=False, status_code=None, html=None, failure_reason=reason
    )


# --------------------------------------------------------------------------- #
# Sitemap sukses (Req 2.1)
# --------------------------------------------------------------------------- #
def test_discover_uses_sitemap_when_valid():
    """Sitemap valid -> URL host-sama dikumpulkan dari sitemap (Req 2.1)."""
    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.com/</loc></url>"
        "<url><loc>https://example.com/about</loc></url>"
        "<url><loc>https://example.com/produk/1</loc></url>"
        "<url><loc>https://other.com/external</loc></url>"
        "</urlset>"
    )
    fetcher = FakeFetcher({"https://example.com/sitemap.xml": _ok(
        "https://example.com/sitemap.xml", sitemap_xml
    )})

    result = asyncio.run(discover_pages(_website(), fetcher))

    assert result.failed is False
    assert result.failure_reason is None
    # URL host-sama dinormalisasi & dedup; URL eksternal diabaikan (Req 2.3).
    assert result.urls == [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/produk/1",
    ]


# --------------------------------------------------------------------------- #
# Fallback ke crawl (Req 2.2, 2.5)
# --------------------------------------------------------------------------- #
def test_discover_falls_back_to_crawl_when_sitemap_missing():
    """Sitemap gagal -> crawl link internal homepage (Req 2.2, 2.5)."""
    homepage_html = """
        <html><body>
          <a href="/about">About</a>
          <a href="/produk/123">Produk</a>
          <a href="https://other.com/ext">Eksternal</a>
          <a href="https://sub.example.com/x">Subdomain</a>
        </body></html>
    """
    about_html = '<html><body><a href="/kontak">Kontak</a></body></html>'

    fetcher = FakeFetcher(
        {
            # sitemap tidak ada -> default 404 (fallback ke crawl)
            "https://example.com/": _ok("https://example.com/", homepage_html),
            "https://example.com/about": _ok(
                "https://example.com/about", about_html
            ),
            "https://example.com/produk/123": _ok(
                "https://example.com/produk/123", "<html></html>"
            ),
            "https://example.com/kontak": _ok(
                "https://example.com/kontak", "<html></html>"
            ),
        }
    )

    result = asyncio.run(discover_pages(_website(), fetcher))

    assert result.failed is False
    # Homepage + halaman produk/kategori & internal lain; eksternal/subdomain diabaikan.
    assert set(result.urls) == {
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/produk/123",
        "https://example.com/kontak",
    }
    # Halaman produk ikut tersertakan (Req 2.5).
    assert "https://example.com/produk/123" in result.urls
    # Tidak ada URL eksternal maupun subdomain (Req 2.3).
    assert all("other.com" not in u and "sub.example.com" not in u for u in result.urls)


# --------------------------------------------------------------------------- #
# Homepage tak dapat diakses (Req 2.7)
# --------------------------------------------------------------------------- #
def test_discover_fails_when_homepage_unreachable():
    """Sitemap gagal & homepage gagal -> failed=True, urls kosong (Req 2.7)."""
    fetcher = FakeFetcher(
        {
            "https://example.com/": _fail(
                "https://example.com/", "koneksi timeout"
            )
        }
    )

    result = asyncio.run(discover_pages(_website(), fetcher))

    assert result.failed is True
    assert result.urls == []
    assert result.failure_reason is not None
    assert "utama" in result.failure_reason.lower()


def test_discover_child_fetch_failure_does_not_stop_discovery():
    """Kegagalan mengambil halaman anak tidak menghentikan crawl (Req 2.7 vs anak)."""
    homepage_html = (
        '<html><body><a href="/a">A</a><a href="/b">B</a></body></html>'
    )
    fetcher = FakeFetcher(
        {
            "https://example.com/": _ok("https://example.com/", homepage_html),
            # /a berhasil (tanpa link lanjutan), /b gagal (default 404) -> tetap disertakan.
            "https://example.com/a": _ok("https://example.com/a", "<html></html>"),
        }
    )

    result = asyncio.run(discover_pages(_website(), fetcher))

    assert result.failed is False
    assert set(result.urls) == {
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    }


def test_discover_falls_back_when_sitemap_has_no_same_host_urls():
    """Sitemap valid tapi hanya berisi URL eksternal -> fallback crawl."""
    sitemap_xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://other.com/a</loc></url>"
        "</urlset>"
    )
    homepage_html = '<html><body><a href="/only">Only</a></body></html>'
    fetcher = FakeFetcher(
        {
            "https://example.com/sitemap.xml": _ok(
                "https://example.com/sitemap.xml", sitemap_xml
            ),
            "https://example.com/": _ok("https://example.com/", homepage_html),
            "https://example.com/only": _ok(
                "https://example.com/only", "<html></html>"
            ),
        }
    )

    result = asyncio.run(discover_pages(_website(), fetcher))

    assert result.failed is False
    assert set(result.urls) == {
        "https://example.com/",
        "https://example.com/only",
    }
