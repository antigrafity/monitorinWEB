"""Tests untuk Hasher domain (task 5).

Berisi property-based tests (Hypothesis, min. 100 iterasi) yang memvalidasi
Correctness Property 13 & 20 pada design, ditambah beberapa unit test contoh
untuk perilaku konkret ``content_hash`` dan ``image_hash``.

- Property 13: determinisme & sensitivitas Content_Hash (Req 5.1).
- Property 20: determinisme & sensitivitas Image_Hash (Req 7.2).
"""

from __future__ import annotations

import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.hashing import content_hash, image_hash


# --------------------------------------------------------------------------- #
# Property 13: Determinisme dan sensitivitas Content_Hash
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 13: Determinisme dan sensitivitas Content_Hash
@settings(max_examples=100)
@given(st.text())
def test_content_hash_deterministic(text: str):
    """Validates: Requirements 5.1

    Determinisme: content_hash menghasilkan nilai yang sama untuk masukan yang
    sama (dipanggil dua kali atas teks identik -> hash identik).
    """
    assert content_hash(text) == content_hash(text)


# Feature: website-monitoring, Property 13: Determinisme dan sensitivitas Content_Hash
@settings(max_examples=100)
@given(st.text(), st.text())
def test_content_hash_sensitive(text_a: str, text_b: str):
    """Validates: Requirements 5.1

    Sensitivitas: untuk dua teks ternormalisasi yang berbeda, content_hash
    menghasilkan nilai yang berbeda (collision SHA-256 praktis mustahil).
    """
    if text_a != text_b:
        assert content_hash(text_a) != content_hash(text_b)
    else:
        assert content_hash(text_a) == content_hash(text_b)


# --------------------------------------------------------------------------- #
# Property 20: Determinisme dan sensitivitas Image_Hash
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 20: Determinisme dan sensitivitas Image_Hash
@settings(max_examples=100)
@given(st.binary())
def test_image_hash_deterministic(content: bytes):
    """Validates: Requirements 7.2

    Determinisme: image_hash menghasilkan nilai yang sama untuk isi biner yang
    sama (dipanggil dua kali atas isi identik -> hash identik).
    """
    assert image_hash(content) == image_hash(content)


# Feature: website-monitoring, Property 20: Determinisme dan sensitivitas Image_Hash
@settings(max_examples=100)
@given(st.binary(), st.binary())
def test_image_hash_sensitive(content_a: bytes, content_b: bytes):
    """Validates: Requirements 7.2

    Sensitivitas: untuk dua isi biner gambar yang berbeda, image_hash
    menghasilkan nilai yang berbeda (collision SHA-256 praktis mustahil).
    """
    if content_a != content_b:
        assert image_hash(content_a) != image_hash(content_b)
    else:
        assert image_hash(content_a) == image_hash(content_b)


# --------------------------------------------------------------------------- #
# Unit tests: contoh konkret
# --------------------------------------------------------------------------- #
def test_content_hash_matches_sha256_hex():
    """content_hash setara SHA-256 hex dari teks UTF-8."""
    text = "halo dunia"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert content_hash(text) == expected


def test_content_hash_empty_string():
    """Teks kosong menghasilkan digest SHA-256 dari byte kosong."""
    expected = hashlib.sha256(b"").hexdigest()
    assert content_hash("") == expected


def test_content_hash_is_hex_of_length_64():
    """Keluaran adalah string hex sepanjang 64 karakter."""
    digest = content_hash("contoh")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_content_hash_unicode():
    """Teks Unicode di-encode UTF-8 sebelum hashing."""
    text = "café — señor ☕"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert content_hash(text) == expected


def test_image_hash_matches_sha256_hex():
    """image_hash setara SHA-256 hex dari isi biner."""
    content = b"\x89PNG\r\n\x1a\n binary image bytes"
    expected = hashlib.sha256(content).hexdigest()
    assert image_hash(content) == expected


def test_image_hash_empty_bytes():
    """Isi biner kosong menghasilkan digest SHA-256 dari byte kosong."""
    expected = hashlib.sha256(b"").hexdigest()
    assert image_hash(b"") == expected


def test_image_hash_is_hex_of_length_64():
    """Keluaran adalah string hex sepanjang 64 karakter."""
    digest = image_hash(b"\x00\x01\x02\x03")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
