"""Klasifikasi & deskripsi tipe perubahan konten (ContentMonitor Tahap 1).

Modul ini berisi fungsi MURNI (tanpa I/O) yang menurunkan tipe perubahan
("added" | "removed" | "updated") dan deskripsi 1 baris Bahasa Indonesia dari
sebuah :class:`~monitoring.domain.models.Diff`, dipakai untuk panel "Top
Changes Detected" pada halaman Overview dan untuk badge tipe pada tabel
perubahan.

Aturan klasifikasi (``classify_change``):

- Hanya ada penambahan (``text_added``/``links_added``/``sections_added``/
  ``images_added`` terisi, sedangkan seluruh bidang "dihapus" kosong DAN
  ``images_changed`` kosong) -> ``"added"``.
- Hanya ada penghapusan (kebalikannya) -> ``"removed"``.
- Campuran penambahan & penghapusan, atau ada ``images_changed`` -> ``"updated"``.
- Diff kosong (tidak ada perubahan apa pun) -> ``"updated"`` (fallback aman;
  seharusnya tidak terjadi pada Change_Event nyata karena Change_Detector
  hanya membuat Change_Event saat ada perbedaan, tetapi fungsi ini tetap harus
  mengembalikan nilai yang valid untuk Diff kosong).

Kompatibel Python 3.9 (``from __future__ import annotations`` + ``typing``).
"""

from __future__ import annotations

from monitoring.domain.models import Diff

# Nilai literal yang dapat dikembalikan classify_change.
CHANGE_TYPE_ADDED = "added"
CHANGE_TYPE_REMOVED = "removed"
CHANGE_TYPE_UPDATED = "updated"

# Panjang potongan teks maksimum pada describe_change sebelum "…" (Tahap 1).
_TRUNCATE_LEN = 60


def _has_additions(diff: Diff) -> bool:
    """True bila ada setidaknya satu bidang "ditambahkan" yang terisi."""
    return bool(
        diff.text_added
        or diff.links_added
        or diff.sections_added
        or diff.images_added
    )


def _has_removals(diff: Diff) -> bool:
    """True bila ada setidaknya satu bidang "dihapus" yang terisi."""
    return bool(
        diff.text_removed
        or diff.links_removed
        or diff.sections_removed
        or diff.images_removed
    )


def classify_change(diff: Diff) -> str:
    """Klasifikasikan sebuah :class:`Diff` sebagai "added"/"removed"/"updated".

    *For any* Diff, hasilnya selalu salah satu dari tiga nilai literal
    (``CHANGE_TYPE_ADDED``, ``CHANGE_TYPE_REMOVED``, ``CHANGE_TYPE_UPDATED``).
    Diff yang HANYA berisi penambahan (tanpa penghapusan/perubahan gambar)
    selalu menghasilkan ``"added"``; Diff yang HANYA berisi penghapusan selalu
    menghasilkan ``"removed"``. Campuran, adanya ``images_changed``, atau Diff
    kosong menghasilkan ``"updated"`` sebagai fallback aman.
    """
    has_additions = _has_additions(diff)
    has_removals = _has_removals(diff)
    has_image_changes = bool(diff.images_changed)

    if has_image_changes:
        return CHANGE_TYPE_UPDATED
    if has_additions and not has_removals:
        return CHANGE_TYPE_ADDED
    if has_removals and not has_additions:
        return CHANGE_TYPE_REMOVED
    # Campuran (keduanya terisi) ATAU keduanya kosong (Diff kosong) -> fallback
    # aman "updated".
    return CHANGE_TYPE_UPDATED


def _truncate(text: str, max_len: int = _TRUNCATE_LEN) -> str:
    """Potong ``text`` menjadi maksimal ``max_len`` karakter, tambahkan "…"."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def describe_change(diff: Diff) -> str:
    """Hasilkan deskripsi 1 baris Bahasa Indonesia untuk sebuah Diff.

    Dipakai pada panel "Top Changes Detected" (Overview) dan tabel Content
    Changes untuk memberi konteks singkat tanpa perlu membuka detail Diff.
    Klausa digabung maksimal 2 dengan " · "; teks panjang dipotong dengan "…".
    """
    clauses = []

    # 1. Perubahan teks: prioritaskan pasangan tunggal "diubah dari...menjadi..."
    if len(diff.text_removed) == 1 and len(diff.text_added) == 1:
        old = _truncate(diff.text_removed[0])
        new = _truncate(diff.text_added[0])
        clauses.append('Teks diubah dari "{0}" menjadi "{1}"'.format(old, new))
    else:
        if diff.text_added and not diff.text_removed:
            n = len(diff.text_added)
            clauses.append(
                "Menambahkan {0} bagian teks baru".format(n)
                if n != 1
                else "Menambahkan 1 bagian teks baru"
            )
        elif diff.text_removed and not diff.text_added:
            n = len(diff.text_removed)
            clauses.append(
                "Menghapus {0} bagian teks".format(n)
                if n != 1
                else "Menghapus 1 bagian teks"
            )
        elif diff.text_added and diff.text_removed:
            clauses.append(
                "Teks berubah ({0} ditambah, {1} dihapus)".format(
                    len(diff.text_added), len(diff.text_removed)
                )
            )

    # 2. Gambar berubah.
    if diff.images_changed:
        n = len(diff.images_changed)
        clauses.append(
            "{0} gambar diperbarui".format(n) if n != 1 else "1 gambar diperbarui"
        )

    # 3. Link ditambahkan/dihapus.
    if diff.links_added or diff.links_removed:
        parts = []
        if diff.links_added:
            parts.append("{0} link ditambahkan".format(len(diff.links_added)))
        if diff.links_removed:
            parts.append("{0} link dihapus".format(len(diff.links_removed)))
        clauses.append(" / ".join(parts))

    # 4. Section ditambahkan/dihapus (hanya bila belum ada klausa lain terkait
    # teks agar tidak berlebihan pada Diff kecil).
    if not clauses and (diff.sections_added or diff.sections_removed):
        parts = []
        if diff.sections_added:
            parts.append("{0} section ditambahkan".format(len(diff.sections_added)))
        if diff.sections_removed:
            parts.append("{0} section dihapus".format(len(diff.sections_removed)))
        clauses.append(" / ".join(parts))

    # 5. Gambar ditambahkan/dihapus (bila belum ada klausa terkait gambar).
    if diff.images_added or diff.images_removed:
        parts = []
        if diff.images_added:
            parts.append("{0} gambar ditambahkan".format(len(diff.images_added)))
        if diff.images_removed:
            parts.append("{0} gambar dihapus".format(len(diff.images_removed)))
        joined = " / ".join(parts)
        if joined not in clauses:
            clauses.append(joined)

    if not clauses:
        return "Tidak ada rincian perubahan."

    # Gabungkan maksimal 2 klausa.
    return " · ".join(clauses[:2])
