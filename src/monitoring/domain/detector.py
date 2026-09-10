"""Change_Detector domain — fungsi murni untuk mendeteksi perubahan (task 6.1).

Membandingkan Snapshot baru dengan Snapshot sebelumnya dan menghasilkan
``DetectionResult`` sesuai Requirement 6, 7, dan 12:

- Tanpa ``previous`` -> baseline awal, tidak ada Change_Event (Req 6.7).
- ``content_hash`` sama & seluruh ``image_hash`` sama -> tidak berubah, tidak
  ada Change_Event (Req 6.1, 6.6, 12.1).
- ``content_hash`` berbeda -> Diff baris teks ditambah/dihapus via ``difflib``
  (Req 6.2).
- Diff link ditambah/dihapus berdasarkan himpunan URL (Req 6.3).
- Diff section ditambah/dihapus berdasarkan blok teks di bawah heading (Req 6.4).
- Diff gambar ditambah/dihapus berdasarkan URL; URL sama tetapi ``image_hash``
  berbeda -> berubah (Req 7.3, 7.4).
- Bila ada perubahan apa pun -> buat tepat satu Change_Event yang mencatat URL,
  waktu deteksi, dan Diff (Req 6.5, 12.2).

Catatan desain (rekonsiliasi section)
--------------------------------------
``Snapshot`` menyimpan ``normalized_text`` (whitespace sudah dikolaps menjadi
satu spasi, tanpa struktur heading) dan BUKAN HTML mentah, sehingga struktur
section tidak dapat direkonstruksi dari ``Snapshot`` saja. Agar fungsi ini
tetap murni dan Property 17 tetap dapat diuji, ``detect_changes`` menerima
daftar blok section yang sudah dihitung sebelumnya (``previous_sections`` dan
``current_sections``) — biasanya dihasilkan oleh CheckOrchestrator memakai
``normalizer.split_sections(html)`` lalu ``Section.as_block()``. Bila tidak
diberikan, diff section dianggap kosong tanpa memengaruhi deteksi perubahan
lain.

Modul ini murni (tanpa I/O) sehingga menjadi target property-based testing
(task 6.2). Kompatibel Python 3.9 melalui ``from __future__ import annotations``
dan ``typing.Optional``.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .models import ChangeEvent, ChangeSummary, Diff, Snapshot


@dataclass(frozen=True)
class DetectionResult:
    """Hasil deteksi perubahan untuk sebuah halaman.

    Attributes:
        changed: ``True`` bila terdeteksi perubahan apa pun (teks, section,
            link, atau gambar); ``False`` untuk baseline maupun kondisi
            tidak-berubah.
        diff: ``Diff`` terkait bila ``changed`` bernilai ``True``; ``None``
            bila baseline atau tidak berubah.
        change_event: Tepat satu ``ChangeEvent`` bila ``changed`` bernilai
            ``True``; ``None`` selain itu (Req 6.5, 6.7, 12.2).
    """

    changed: bool
    diff: Optional[Diff]
    change_event: Optional[ChangeEvent]


def _diff_text_lines(old_text: str, new_text: str) -> "tuple[List[str], List[str]]":
    """Hitung baris teks yang ditambah/dihapus memakai ``difflib`` (Req 6.2).

    Teks ternormalisasi dipecah menjadi baris lalu dibandingkan dengan
    ``difflib.SequenceMatcher``. Baris pada blok ``insert``/``replace`` dari
    sisi teks baru dianggap ditambahkan; baris pada blok ``delete``/``replace``
    dari sisi teks lama dianggap dihapus. Deterministik untuk masukan yang sama.

    Returns:
        Pasangan ``(text_added, text_removed)``.
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added: List[str] = []
    removed: List[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])
    return added, removed


def _set_difference_preserving_order(
    primary: List[str], other: List[str]
) -> List[str]:
    """Kembalikan elemen ``primary`` yang tidak ada pada ``other``.

    Mempertahankan urutan kemunculan pada ``primary`` dan menghapus duplikat
    (kemunculan pertama dipertahankan) sehingga hasilnya deterministik dan
    berperilaku sebagai selisih himpunan.
    """
    other_set = set(other)
    seen: set = set()
    result: List[str] = []
    for item in primary:
        if item in other_set or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _diff_links(
    previous: Snapshot, current: Snapshot
) -> "tuple[List[str], List[str]]":
    """Deteksi link ditambah/dihapus berdasarkan himpunan URL (Req 6.3)."""
    links_added = _set_difference_preserving_order(current.links, previous.links)
    links_removed = _set_difference_preserving_order(previous.links, current.links)
    return links_added, links_removed


def _diff_sections(
    previous_sections: List[str], current_sections: List[str]
) -> "tuple[List[str], List[str]]":
    """Deteksi section ditambah/dihapus sebagai selisih himpunan blok (Req 6.4)."""
    sections_added = _set_difference_preserving_order(
        current_sections, previous_sections
    )
    sections_removed = _set_difference_preserving_order(
        previous_sections, current_sections
    )
    return sections_added, sections_removed


def _diff_images(
    previous: Snapshot, current: Snapshot
) -> "tuple[List[str], List[str], List[str]]":
    """Deteksi gambar ditambah/dihapus/berubah (Req 7.3, 7.4).

    - ``images_added``: URL yang hanya ada pada Snapshot baru.
    - ``images_removed``: URL yang hanya ada pada Snapshot lama.
    - ``images_changed``: URL yang ada pada keduanya namun ``image_hash``-nya
      berbeda (URL sama, isi berbeda).
    """
    prev_images = previous.image_hashes
    cur_images = current.image_hashes

    images_added = [url for url in cur_images if url not in prev_images]
    images_removed = [url for url in prev_images if url not in cur_images]
    images_changed = [
        url
        for url in cur_images
        if url in prev_images and prev_images[url] != cur_images[url]
    ]
    return images_added, images_removed, images_changed


def _diff_has_changes(diff: Diff) -> bool:
    """``True`` bila ``Diff`` memuat perubahan apa pun pada salah satu bidang."""
    return any(
        (
            diff.text_added,
            diff.text_removed,
            diff.links_added,
            diff.links_removed,
            diff.sections_added,
            diff.sections_removed,
            diff.images_added,
            diff.images_removed,
            diff.images_changed,
            diff.title_changed is not None,
            diff.meta_changed is not None,
        )
    )


def _diff_title_meta(
    previous: Snapshot, current: Snapshot
) -> "tuple[Optional[tuple], Optional[tuple]]":
    """Deteksi perubahan judul (<title>) & meta description (Tahap 3).

    Mengembalikan pasangan ``(title_changed, meta_changed)``, masing-masing
    ``None`` bila nilainya tidak berubah antara ``previous`` dan ``current``,
    atau ``(lama, baru)`` bila berubah. Dua nilai ``None`` dianggap tidak
    berubah (bukan berubah dari ``None`` ke ``None``).
    """
    title_changed = None
    if previous.title != current.title:
        title_changed = (previous.title, current.title)
    meta_changed = None
    if previous.meta_description != current.meta_description:
        meta_changed = (previous.meta_description, current.meta_description)
    return title_changed, meta_changed


def _build_summary(diff: Diff) -> ChangeSummary:
    """Bangun ringkasan jumlah perubahan dari ``Diff`` (Req 9.2).

    Ringkasan lengkap untuk notifikasi diimplementasikan pada task 11.1
    (``notifier.build_summary``); di sini disediakan ringkasan minimal agar
    ``ChangeEvent`` yang dibuat konsisten dan lengkap (Req 6.5).
    """
    return ChangeSummary(
        text_added=len(diff.text_added),
        text_removed=len(diff.text_removed),
        links_added=len(diff.links_added),
        links_removed=len(diff.links_removed),
        images_changed=len(diff.images_changed),
    )


def detect_changes(
    previous: Optional[Snapshot],
    current: Snapshot,
    previous_sections: Optional[List[str]] = None,
    current_sections: Optional[List[str]] = None,
    detected_at: Optional[datetime] = None,
    event_id: Optional[str] = None,
) -> DetectionResult:
    """Bandingkan Snapshot baru dengan sebelumnya dan hasilkan DetectionResult.

    Args:
        previous: Snapshot sebelumnya, atau ``None`` bila belum ada (baseline).
        current: Snapshot baru yang akan dibandingkan.
        previous_sections: Daftar blok section (``Section.as_block()``) dari
            halaman versi sebelumnya. Bila ``None`` dianggap kosong.
        current_sections: Daftar blok section dari halaman versi saat ini. Bila
            ``None`` dianggap kosong.
        detected_at: Waktu deteksi untuk Change_Event. Bila ``None`` memakai
            ``current.checked_at`` agar hasil deterministik dan mudah diuji
            (Req 6.5).
        event_id: ID Change_Event. Bila ``None`` dibuat memakai ``uuid4``.

    Returns:
        ``DetectionResult`` sesuai aturan deteksi (Req 6.1–6.7, 7.3, 7.4,
        12.1, 12.2).
    """
    # Req 6.7: tanpa Snapshot sebelumnya -> baseline, tidak ada Change_Event.
    if previous is None:
        return DetectionResult(changed=False, diff=None, change_event=None)

    # Req 6.1, 6.6, 12.1: content_hash sama & seluruh image_hash sama ->
    # halaman tidak berubah, tidak membuat Change_Event. PENTING (Tahap 3):
    # title/meta_description TIDAK ikut dalam normalized_text/content_hash,
    # sehingga gerbang ini juga memeriksa keduanya secara eksplisit — bila
    # salah satunya berubah, halaman TETAP dianggap berubah meski content_hash
    # & image_hashes identik (mis. kasus "hanya title berubah").
    title_changed, meta_changed = _diff_title_meta(previous, current)
    if (
        current.content_hash == previous.content_hash
        and current.image_hashes == previous.image_hashes
        and title_changed is None
        and meta_changed is None
    ):
        return DetectionResult(changed=False, diff=None, change_event=None)

    prev_sections = previous_sections if previous_sections is not None else []
    cur_sections = current_sections if current_sections is not None else []

    # Hitung seluruh dimensi perbedaan.
    text_added, text_removed = _diff_text_lines(
        previous.normalized_text, current.normalized_text
    )
    links_added, links_removed = _diff_links(previous, current)
    sections_added, sections_removed = _diff_sections(prev_sections, cur_sections)
    images_added, images_removed, images_changed = _diff_images(previous, current)

    diff = Diff(
        text_added=text_added,
        text_removed=text_removed,
        links_added=links_added,
        links_removed=links_removed,
        sections_added=sections_added,
        sections_removed=sections_removed,
        images_added=images_added,
        images_removed=images_removed,
        images_changed=images_changed,
        title_changed=title_changed,
        meta_changed=meta_changed,
    )

    changed = _diff_has_changes(diff)
    if not changed:
        # Pertahanan defensif: gate hash mensyaratkan adanya perbedaan, sehingga
        # kondisi ini praktis tak tercapai. Bila diff kosong, perlakukan sebagai
        # tidak berubah agar tidak membuat Change_Event tanpa perubahan nyata.
        return DetectionResult(changed=False, diff=None, change_event=None)

    # Req 6.5, 12.2: tepat satu Change_Event yang mencatat URL, waktu deteksi, Diff.
    event = ChangeEvent(
        id=event_id if event_id is not None else str(uuid.uuid4()),
        website_id=current.website_id,
        url=current.url,
        detected_at=detected_at if detected_at is not None else current.checked_at,
        diff=diff,
        summary=_build_summary(diff),
    )

    return DetectionResult(changed=True, diff=diff, change_event=event)
