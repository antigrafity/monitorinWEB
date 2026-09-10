"""Fungsi murni pencocokan kata kunci untuk ContentMonitor Tahap 4.

Modul ini berisi:
- ``match_keywords(text_blocks, keywords) -> List[KeywordMatch]`` — fungsi murni
  yang memeriksa keberadaan setiap kata kunci (case-insensitive) pada blok
  teks halaman.
"""

from __future__ import annotations

from typing import List

from .models import Keyword, KeywordMatch


def match_keywords(
    text_blocks: List[str],
    keywords: List[Keyword],
) -> List[KeywordMatch]:
    """Cocokkan daftar kata kunci terhadap blok teks sebuah halaman.

    Pencocokan dilakukan secara case-insensitive: kata kunci dianggap
    ditemukan bila bentuk huruf kecilnya merupakan substring dari bentuk huruf
    kecil minimal satu blok teks dalam ``text_blocks``.

    Jaminan semantik:
    - Kata kunci yang ada di salah satu blok selalu terdeteksi ada (found=True).
    - Kata kunci yang tidak ada di seluruh blok terdeteksi hilang (found=False).
    - Pencocokan tidak peka huruf besar/kecil.

    Args:
        text_blocks: Daftar blok teks halaman yang akan diperiksa.
        keywords: Daftar objek :class:`Keyword` yang akan dicocokkan.

    Returns:
        Daftar objek :class:`KeywordMatch` yang berkorespondensi 1-ke-1
        dengan urutan ``keywords`` input.
    """
    if not keywords:
        return []

    # Konversi setiap blok menjadi lowercase satu kali demi efisiensi.
    lower_blocks = [b.lower() for b in text_blocks]

    results: List[KeywordMatch] = []
    for kw in keywords:
        target = kw.keyword.lower()
        found = any(target in block for block in lower_blocks)
        results.append(KeywordMatch(keyword=kw, found=found))

    return results
