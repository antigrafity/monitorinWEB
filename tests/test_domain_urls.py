"""Unit test contoh untuk util URL Page_Discovery (task 7.1).

Memverifikasi perilaku konkret ``normalize_url``, ``is_same_host``,
``dedup_urls``, dan ``parse_sitemap`` sesuai Requirement 2.3 dan 2.4:
kanonikalisasi URL, penyaringan host sama persis, deduplikasi berurutan, serta
penguraian sitemap (``urlset``/``sitemapindex``) dan penanganan format tak valid.

Property test menyeluruh (Property 4 & 5) dibuat pada task 7.2; di sini disertakan
contoh dasar untuk setiap fungsi.
"""

from __future__ import annotations

from monitoring.domain.urls import (
    dedup_urls,
    is_same_host,
    normalize_url,
    parse_sitemap,
)

# --------------------------------------------------------------------------- #
# normalize_url (Req 2.4)
# --------------------------------------------------------------------------- #


def test_normalize_url_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_url_removes_fragment():
    assert normalize_url("https://example.com/a#section") == "https://example.com/a"


def test_normalize_url_empty_path_becomes_root_slash():
    assert normalize_url("https://example.com") == "https://example.com/"


def test_normalize_url_strips_trailing_slash_on_non_root():
    assert normalize_url("https://example.com/about/") == "https://example.com/about"


def test_normalize_url_root_slash_preserved():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_url_preserves_query_string():
    assert (
        normalize_url("https://example.com/search/?q=Hello")
        == "https://example.com/search?q=Hello"
    )


def test_normalize_url_is_deterministic():
    url = "HTTPS://Example.com/A/#top"
    assert normalize_url(url) == normalize_url(url)


def test_normalize_url_dedup_use_case():
    """Dua bentuk URL yang setara mengkanonikalisasi ke nilai yang sama."""
    a = normalize_url("https://Example.com/about/#x")
    b = normalize_url("https://example.com/about")
    assert a == b


# --------------------------------------------------------------------------- #
# is_same_host (Req 2.3)
# --------------------------------------------------------------------------- #


def test_is_same_host_exact_match_true():
    assert is_same_host("https://example.com/page", "example.com") is True


def test_is_same_host_case_insensitive():
    assert is_same_host("https://EXAMPLE.com/page", "example.com") is True


def test_is_same_host_different_subdomain_rejected():
    assert is_same_host("https://blog.example.com/page", "example.com") is False


def test_is_same_host_external_domain_rejected():
    assert is_same_host("https://other.com/page", "example.com") is False


def test_is_same_host_base_given_as_full_url():
    assert is_same_host("https://example.com/x", "https://example.com/") is True


def test_is_same_host_base_with_port_ignored():
    assert is_same_host("https://example.com/x", "example.com:8080") is True


def test_is_same_host_relative_candidate_false():
    assert is_same_host("/about", "example.com") is False


def test_is_same_host_empty_inputs_false():
    assert is_same_host("", "example.com") is False
    assert is_same_host("https://example.com", "") is False


# --------------------------------------------------------------------------- #
# dedup_urls (Req 2.4)
# --------------------------------------------------------------------------- #


def test_dedup_urls_empty_list():
    assert dedup_urls([]) == []


def test_dedup_urls_no_duplicates_unchanged():
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    assert dedup_urls(urls) == urls


def test_dedup_urls_removes_duplicates_preserving_first_order():
    urls = [
        "https://a.com/1",
        "https://a.com/2",
        "https://a.com/1",
        "https://a.com/3",
        "https://a.com/2",
    ]
    assert dedup_urls(urls) == [
        "https://a.com/1",
        "https://a.com/2",
        "https://a.com/3",
    ]


def test_dedup_urls_is_idempotent():
    urls = ["b", "a", "b", "c", "a"]
    once = dedup_urls(urls)
    assert dedup_urls(once) == once


# --------------------------------------------------------------------------- #
# parse_sitemap (Req 2.1)
# --------------------------------------------------------------------------- #

_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def test_parse_sitemap_urlset_returns_locs():
    xml = (
        f"<urlset {_NS}>"
        "<url><loc>https://example.com/</loc></url>"
        "<url><loc>https://example.com/about</loc></url>"
        "</urlset>"
    ).encode("utf-8")
    assert parse_sitemap(xml) == [
        "https://example.com/",
        "https://example.com/about",
    ]


def test_parse_sitemap_sitemapindex_returns_locs():
    xml = (
        f"<sitemapindex {_NS}>"
        "<sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>"
        "<sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>"
        "</sitemapindex>"
    ).encode("utf-8")
    assert parse_sitemap(xml) == [
        "https://example.com/sitemap1.xml",
        "https://example.com/sitemap2.xml",
    ]


def test_parse_sitemap_without_namespace():
    xml = b"<urlset><url><loc>https://example.com/x</loc></url></urlset>"
    assert parse_sitemap(xml) == ["https://example.com/x"]


def test_parse_sitemap_accepts_str_input():
    xml = "<urlset><url><loc>https://example.com/s</loc></url></urlset>"
    assert parse_sitemap(xml) == ["https://example.com/s"]


def test_parse_sitemap_valid_empty_urlset_returns_empty_list():
    xml = f"<urlset {_NS}></urlset>".encode("utf-8")
    assert parse_sitemap(xml) == []


def test_parse_sitemap_trims_loc_whitespace_and_skips_empty():
    xml = (
        "<urlset>"
        "<url><loc>  https://example.com/a  </loc></url>"
        "<url><loc>   </loc></url>"
        "</urlset>"
    ).encode("utf-8")
    assert parse_sitemap(xml) == ["https://example.com/a"]


def test_parse_sitemap_none_for_empty_input():
    assert parse_sitemap(b"") is None
    assert parse_sitemap("") is None


def test_parse_sitemap_none_for_malformed_xml():
    assert parse_sitemap(b"<urlset><url><loc>oops") is None


def test_parse_sitemap_none_for_non_sitemap_root():
    assert parse_sitemap(b"<html><body>hi</body></html>") is None


def test_parse_sitemap_none_for_plain_text():
    assert parse_sitemap(b"just some text, not xml") is None
