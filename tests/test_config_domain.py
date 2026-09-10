"""Test validasi format domain (task 3.1).

Berisi:

- Property-based test (Hypothesis, min 100 iterasi) yang memvalidasi
  Property 1 pada design: ``validate_domain`` menerima sebuah string jika dan
  hanya jika panjangnya 1\u2013253 karakter, tersusun atas label tak-kosong yang
  dipisahkan titik, dan setiap label hanya berisi huruf, angka, atau tanda
  hubung; selain itu ditolak dengan pesan yang menjelaskan format.
- Generator yang memproduksi BOTH domain valid & invalid.
- Unit test contoh spesifik & edge-case.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.config import (
    DOMAIN_FORMAT_ERROR,
    DOMAIN_MAX_LEN,
    validate_domain,
)

# --------------------------------------------------------------------------- #
# Referensi kriteria valid (independen dari implementasi) untuk oracle test.
# --------------------------------------------------------------------------- #
_ALLOWED_LABEL_CHARS = frozenset(string.ascii_letters + string.digits + "-")


def _is_valid_domain(domain: str) -> bool:
    """Oracle referensi kriteria Property 1, ditulis mandiri dari implementasi."""
    if not (1 <= len(domain) <= DOMAIN_MAX_LEN):
        return False
    labels = domain.split(".")
    for label in labels:
        if label == "":
            return False
        if any(ch not in _ALLOWED_LABEL_CHARS for ch in label):
            return False
    return True


# --------------------------------------------------------------------------- #
# Generator: domain valid & invalid bercampur.
# --------------------------------------------------------------------------- #
_label_chars = st.text(alphabet=string.ascii_letters + string.digits + "-", min_size=1, max_size=20)


@st.composite
def _valid_domains(draw) -> str:
    """Domain yang pasti valid: label tak-kosong dari [A-Za-z0-9-] dipisah titik."""
    labels = draw(st.lists(_label_chars, min_size=1, max_size=6))
    domain = ".".join(labels)
    # Batasi panjang agar tetap dalam 1..253.
    domain = domain[:DOMAIN_MAX_LEN]
    # Setelah pemotongan, pastikan tidak berakhir dengan titik (label kosong).
    while domain.endswith("."):
        domain = domain[:-1]
    # Jaga invariant minimal 1 karakter.
    if domain == "":
        domain = "a"
    return domain


# Karakter yang berpotensi membuat domain invalid (di luar [A-Za-z0-9-.]).
_invalid_chars = st.sampled_from(list(" _@!#$%/\\:*?<>|~+=,'\"()[]{}"))


@st.composite
def _maybe_invalid_domains(draw) -> str:
    """String bebas yang dapat valid maupun invalid; digabung dengan karakter aneh,
    titik di tepi, titik ganda, string kosong, dan string sangat panjang."""
    strategy = draw(
        st.sampled_from(["free", "edge_dot", "double_dot", "with_bad_char", "empty", "too_long"])
    )
    base = draw(st.text(alphabet=string.ascii_letters + string.digits + "-.", max_size=30))
    if strategy == "free":
        return base
    if strategy == "edge_dot":
        return "." + base + "."
    if strategy == "double_dot":
        return base + ".." + base
    if strategy == "with_bad_char":
        bad = draw(_invalid_chars)
        pos = draw(st.integers(min_value=0, max_value=len(base)))
        return base[:pos] + bad + base[pos:]
    if strategy == "empty":
        return ""
    # too_long
    return draw(st.text(alphabet="a.", min_size=DOMAIN_MAX_LEN + 1, max_size=DOMAIN_MAX_LEN + 50))


_domains = st.one_of(_valid_domains(), _maybe_invalid_domains(), st.text(max_size=260))


# --------------------------------------------------------------------------- #
# Property 1: Validasi format domain
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 1: Validasi format domain
@settings(max_examples=300)
@given(_domains)
def test_validate_domain_accepts_iff_criteria_hold(domain: str):
    """Validates: Requirements 1.4

    ``validate_domain`` menerima string jika dan hanya jika memenuhi kriteria
    (panjang 1\u2013253, label tak-kosong dipisah titik, hanya huruf/angka/tanda
    hubung). Bila ditolak, disertai pesan format yang diharapkan.
    """
    result = validate_domain(domain)
    expected_ok = _is_valid_domain(domain)

    assert result.ok is expected_ok
    if expected_ok:
        assert result.error_message is None
    else:
        assert result.error_message == DOMAIN_FORMAT_ERROR


# Feature: website-monitoring, Property 1: Validasi format domain
@settings(max_examples=200)
@given(_valid_domains())
def test_valid_domains_always_accepted(domain: str):
    """Validates: Requirements 1.4"""
    result = validate_domain(domain)
    assert result.ok is True
    assert result.error_message is None


# --------------------------------------------------------------------------- #
# Unit test contoh spesifik & edge-case.
# --------------------------------------------------------------------------- #
def test_accepts_simple_domain():
    assert validate_domain("example.com").ok is True


def test_accepts_subdomain_and_hyphen():
    assert validate_domain("my-site.co.uk").ok is True
    assert validate_domain("a1-b2.example-domain.org").ok is True


def test_accepts_single_label():
    assert validate_domain("localhost").ok is True


def test_accepts_max_length_domain():
    # Tepat 253 karakter tetap valid.
    domain = ("a" * 49 + ".") * 5 + "a" * 3  # 5*50 + 3 = 253
    assert len(domain) == DOMAIN_MAX_LEN
    assert validate_domain(domain).ok is True


def test_rejects_empty_string():
    result = validate_domain("")
    assert result.ok is False
    assert result.error_message == DOMAIN_FORMAT_ERROR


def test_rejects_too_long_domain():
    domain = "a" * (DOMAIN_MAX_LEN + 1)
    assert validate_domain(domain).ok is False


def test_rejects_leading_dot():
    assert validate_domain(".example.com").ok is False


def test_rejects_trailing_dot():
    assert validate_domain("example.com.").ok is False


def test_rejects_double_dot():
    assert validate_domain("example..com").ok is False


def test_rejects_underscore():
    assert validate_domain("exa_mple.com").ok is False


def test_rejects_space():
    assert validate_domain("exa mple.com").ok is False


def test_rejects_special_characters():
    for bad in ["exa@mple.com", "example.com/path", "http://example.com", "ex*ample.com"]:
        assert validate_domain(bad).ok is False


def test_rejects_non_ascii_letters():
    # Huruf non-ASCII (mis. Cyrillic) tidak dianggap valid meski ``isalnum``.
    assert validate_domain("\u0430pple.com").ok is False


def test_error_message_explains_format():
    result = validate_domain("bad_domain")
    assert result.ok is False
    assert "1\u2013253" in result.error_message
    assert "titik" in result.error_message
