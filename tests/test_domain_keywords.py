"""Property-based & unit tests untuk pencocokan kata kunci (ContentMonitor Tahap 4).

Menguji:
# Feature: contentmonitor, Property: pencocokan kata kunci
- Kata kunci yang ada di salah satu blok selalu terdeteksi ada.
- Kata kunci yang tidak ada di blok mana pun selalu terdeteksi hilang.
- Pencocokan tidak peka huruf besar/kecil (case-insensitive).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.keywords import match_keywords
from monitoring.domain.models import Keyword


def _make_keyword(kw_text: str, mode: str = "must_exist") -> Keyword:
    return Keyword(
        id=str(uuid4()),
        website_id="test-website-id",
        keyword=kw_text,
        mode=mode,
        created_at=datetime.now(timezone.utc),
    )


# --- Unit Tests ------------------------------------------------------------- #


def test_match_keywords_empty():
    """Menguji perilaku dengan blok atau kata kunci kosong."""
    kw = _make_keyword("diskon")
    assert match_keywords([], [kw])[0].found is False
    assert match_keywords(["Selamat datang"], []) == []


def test_match_keywords_case_insensitive_basic():
    """Menguji pencocokan case-insensitive sederhana."""
    kw_diskon = _make_keyword("DISKON")
    kw_gratis = _make_keyword("gratis")
    kw_promo = _make_keyword("Promo")

    blocks = ["Kami memberikan diskon besar-besaran hari ini."]
    results = match_keywords(blocks, [kw_diskon, kw_gratis, kw_promo])

    assert len(results) == 3
    assert results[0].keyword == kw_diskon and results[0].found is True
    assert results[1].keyword == kw_gratis and results[1].found is False
    assert results[2].keyword == kw_promo and results[2].found is False


# --- Property-Based Tests (Hypothesis) --------------------------------------- #
# Feature: contentmonitor, Property: pencocokan kata kunci


@settings(max_examples=100)
@given(
    st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    st.text(min_size=0, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "),
    st.text(min_size=0, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "),
    st.booleans(),
)
def test_property_keyword_present_always_found(
    target_kw: str, prefix: str, suffix: str, upper_case: bool
):
    """# Feature: contentmonitor, Property: pencocokan kata kunci
    
    Kata kunci yang ada di salah satu blok (meski berbeda case) selalu
    terdeteksi ada (found=True).
    """
    block_text = f"{prefix} {target_kw.upper() if upper_case else target_kw.lower()} {suffix}"
    kw = _make_keyword(target_kw)

    matches = match_keywords([block_text], [kw])
    assert len(matches) == 1
    assert matches[0].found is True


@settings(max_examples=100)
@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10),
        min_size=1,
        max_size=5,
    ),
    st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5),
)
def test_property_keyword_missing_always_not_found(
    blocks: List[str], target_upper: str
):
    """# Feature: contentmonitor, Property: pencocokan kata kunci
    
    Kata kunci yang tidak ada di blok mana pun selalu terdeteksi hilang
    (found=False).
    """
    # Pastikan target_upper (yang hanya huruf kapital) bentuk huruf kecilnya
    # benar-benar tidak ada di blok mana pun
    target_lower = target_upper.lower()
    clean_blocks = [
        b.replace(target_lower, "") for b in blocks
    ]

    kw = _make_keyword(target_upper)
    matches = match_keywords(clean_blocks, [kw])
    assert len(matches) == 1
    assert matches[0].found is False


@settings(max_examples=100)
@given(
    st.text(min_size=2, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    st.text(min_size=0, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ "),
)
def test_property_case_insensitivity_equivalence(kw_str: str, text: str):
    """# Feature: contentmonitor, Property: pencocokan kata kunci
    
    Pencocokan tidak peka huruf besar/kecil: hasil pencocokan kw_str dan
    kw_str.upper() serta kw_str.lower() selalu identik terhadap teks yang sama.
    """
    kw_original = _make_keyword(kw_str)
    kw_upper = _make_keyword(kw_str.upper())
    kw_lower = _make_keyword(kw_str.lower())

    results = match_keywords([text], [kw_original, kw_upper, kw_lower])
    assert results[0].found == results[1].found == results[2].found
