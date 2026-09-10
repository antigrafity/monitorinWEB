"""Pembangkit Alert — fungsi MURNI (tanpa I/O) untuk ContentMonitor Tahap 3.

Modul ini menghasilkan daftar :class:`~monitoring.domain.models.Alert` dari
data yang sudah tersedia (``Diff``, hasil fetch, tanggal kadaluarsa SSL, dsb.)
TANPA melakukan I/O maupun akses Data_Store apa pun — seluruh fungsi menerima
data biasa dan mengembalikan ``List[Alert]`` (atau ``Optional[Alert]`` untuk
detektor tunggal), sehingga mudah diuji dengan unit test maupun
property-based test.

Sumber Alert yang didukung:

1. Halaman gagal diakses -> severity 'critical'
   (:func:`alert_for_page_unreachable`).
2. Penghapusan konten signifikan -> severity 'warning'. Ambang: jumlah blok
   teks dihapus >= ``SIGNIFICANT_REMOVAL_BLOCKS`` ATAU proporsi blok halaman
   yang hilang >= ``SIGNIFICANT_REMOVAL_RATIO`` (50%) — mana pun tercapai
   lebih dulu (:func:`alert_for_significant_removal`).
3. Judul halaman (<title>) berubah -> severity 'info'
   (:func:`alert_for_title_changed`).
4. Meta description berubah -> severity 'info'
   (:func:`alert_for_meta_changed`).
5. Perubahan struktur heading (section ditambah/dihapus) -> severity 'info'
   (:func:`alert_for_heading_structure_changed`).
6. Konten baru terdeteksi (hanya penambahan, tanpa penghapusan sama sekali)
   -> severity 'info' (:func:`alert_for_new_content`).
7. Sitemap tidak dapat diakses -> severity 'warning'
   (:func:`alert_for_sitemap_unreachable`).
8. SSL akan kadaluarsa -> 'warning' bila < 30 hari, 'critical' bila < 7 hari
   (:func:`alert_for_ssl_expiring`).

:func:`generate_page_alerts` menggabungkan detektor #2–#6 (yang bergantung
pada ``Diff`` sebuah halaman) menjadi satu pemanggilan praktis untuk
``CheckOrchestrator``.

Kompatibel Python 3.9 melalui ``from __future__ import annotations`` dan
``typing.Optional``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from monitoring.config import (
    SIGNIFICANT_REMOVAL_BLOCKS,
    SIGNIFICANT_REMOVAL_RATIO,
    SSL_EXPIRY_CRITICAL_DAYS,
    SSL_EXPIRY_WARNING_DAYS,
)
from monitoring.domain.models import Alert, Diff

# Nilai literal severity & status yang valid (selaras skema Data_Store).
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

ALERT_TYPE_PAGE_UNREACHABLE = "page_unreachable"
ALERT_TYPE_SIGNIFICANT_REMOVAL = "significant_removal"
ALERT_TYPE_TITLE_CHANGED = "title_changed"
ALERT_TYPE_META_CHANGED = "meta_changed"
ALERT_TYPE_HEADING_STRUCTURE_CHANGED = "heading_structure_changed"
ALERT_TYPE_NEW_CONTENT = "new_content"
ALERT_TYPE_SITEMAP_UNREACHABLE = "sitemap_unreachable"
ALERT_TYPE_SSL_EXPIRING = "ssl_expiring"


def _new_id() -> str:
    """Bangun ID Alert baru (UUID4)."""
    return str(uuid.uuid4())


def _now_if_none(triggered_at: Optional[datetime]) -> datetime:
    """Kembalikan ``triggered_at`` bila diberikan, selain itu ``datetime.now()``.

    Parameter opsional ini memudahkan pengujian deterministik (Alert
    generator tetap murni: pemanggil yang menyediakan waktu untuk hasil yang
    dapat diprediksi; bila tidak, waktu saat ini dipakai sebagai kenyamanan).
    """
    return triggered_at if triggered_at is not None else datetime.now()


# --------------------------------------------------------------------------- #
# 1. Halaman gagal diakses -> 'critical'
# --------------------------------------------------------------------------- #
def alert_for_page_unreachable(
    website_id: str,
    url: str,
    status_code: Optional[int] = None,
    reason: Optional[str] = None,
    triggered_at: Optional[datetime] = None,
) -> Alert:
    """Bangun Alert 'critical' untuk halaman yang gagal diakses.

    Judul menyertakan kode status bila tersedia (mis. "Halaman tidak dapat
    diakses (404)"); bila tidak ada kode status (mis. timeout/error koneksi),
    judul tetap dibentuk tanpa kode.
    """
    if status_code is not None:
        title = f"Halaman tidak dapat diakses ({status_code})"
    else:
        title = "Halaman tidak dapat diakses"
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_PAGE_UNREACHABLE,
        severity=SEVERITY_CRITICAL,
        title=title,
        detail=reason,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 2. Penghapusan konten signifikan -> 'warning'
# --------------------------------------------------------------------------- #
def is_significant_removal(
    removed_count: int,
    previous_block_count: int,
    threshold_blocks: int = SIGNIFICANT_REMOVAL_BLOCKS,
    threshold_ratio: float = SIGNIFICANT_REMOVAL_RATIO,
) -> bool:
    """Tentukan apakah jumlah blok teks dihapus dianggap signifikan.

    ``True`` jika dan hanya jika ``removed_count >= threshold_blocks`` ATAU
    (bila ``previous_block_count > 0``) proporsi blok dihapus terhadap total
    blok halaman sebelumnya ``>= threshold_ratio``. Bila
    ``previous_block_count <= 0``, hanya ambang jumlah absolut yang berlaku
    (menghindari pembagian dengan nol).
    """
    if removed_count < 0:
        removed_count = 0
    if removed_count >= threshold_blocks:
        return True
    if previous_block_count > 0:
        ratio = removed_count / previous_block_count
        if ratio >= threshold_ratio:
            return True
    return False


def alert_for_significant_removal(
    website_id: str,
    url: str,
    diff: Diff,
    previous_block_count: int,
    triggered_at: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert 'warning' bila penghapusan teks dianggap signifikan.

    Ambang: lihat :func:`is_significant_removal`. Mengembalikan ``None`` bila
    penghapusan tidak signifikan (termasuk saat tidak ada penghapusan sama
    sekali).
    """
    removed_count = len(diff.text_removed)
    if not is_significant_removal(removed_count, previous_block_count):
        return None
    detail = (
        f"{removed_count} blok teks dihapus dari {previous_block_count} blok "
        "sebelumnya."
    )
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_SIGNIFICANT_REMOVAL,
        severity=SEVERITY_WARNING,
        title="Penghapusan konten signifikan",
        detail=detail,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 3. Judul halaman berubah -> 'info'
# --------------------------------------------------------------------------- #
def alert_for_title_changed(
    website_id: str,
    url: str,
    diff: Diff,
    triggered_at: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert 'info' bila ``diff.title_changed`` terisi.

    Mengembalikan ``None`` bila judul tidak berubah (``title_changed is
    None``). Detail menyertakan judul lama & baru.
    """
    if diff.title_changed is None:
        return None
    old_title, new_title = diff.title_changed
    detail = 'Judul lama: "{0}" -> Judul baru: "{1}"'.format(
        old_title or "(tidak ada)", new_title or "(tidak ada)"
    )
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_TITLE_CHANGED,
        severity=SEVERITY_INFO,
        title="Judul halaman berubah",
        detail=detail,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 4. Meta description berubah -> 'info'
# --------------------------------------------------------------------------- #
def alert_for_meta_changed(
    website_id: str,
    url: str,
    diff: Diff,
    triggered_at: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert 'info' bila ``diff.meta_changed`` terisi.

    Mengembalikan ``None`` bila meta description tidak berubah.
    """
    if diff.meta_changed is None:
        return None
    old_meta, new_meta = diff.meta_changed
    detail = 'Meta lama: "{0}" -> Meta baru: "{1}"'.format(
        old_meta or "(tidak ada)", new_meta or "(tidak ada)"
    )
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_META_CHANGED,
        severity=SEVERITY_INFO,
        title="Meta description berubah",
        detail=detail,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 5. Perubahan struktur heading (section ditambah/dihapus) -> 'info'
# --------------------------------------------------------------------------- #
def alert_for_heading_structure_changed(
    website_id: str,
    url: str,
    diff: Diff,
    triggered_at: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert 'info' bila ada section ditambah/dihapus.

    Mengembalikan ``None`` bila tidak ada perubahan section sama sekali.
    """
    added = len(diff.sections_added)
    removed = len(diff.sections_removed)
    if added == 0 and removed == 0:
        return None
    parts = []
    if added:
        parts.append(f"{added} section ditambahkan")
    if removed:
        parts.append(f"{removed} section dihapus")
    detail = "; ".join(parts)
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_HEADING_STRUCTURE_CHANGED,
        severity=SEVERITY_INFO,
        title="Struktur heading berubah",
        detail=detail,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 6. Konten baru terdeteksi (hanya penambahan) -> 'info'
# --------------------------------------------------------------------------- #
def alert_for_new_content(
    website_id: str,
    url: str,
    diff: Diff,
    triggered_at: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert 'info' bila Diff HANYA berisi penambahan konten.

    "Hanya penambahan" berarti seluruh bidang "dihapus"/"berubah" pada Diff
    kosong (``text_removed``, ``links_removed``, ``sections_removed``,
    ``images_removed``, ``images_changed``) DAN setidaknya satu bidang
    "ditambahkan" terisi (``text_added``, ``links_added``, ``sections_added``,
    ``images_added``). Mengembalikan ``None`` bila kondisi tidak terpenuhi
    (termasuk Diff yang sama sekali tidak memuat penambahan).
    """
    has_removal_or_change = bool(
        diff.text_removed
        or diff.links_removed
        or diff.sections_removed
        or diff.images_removed
        or diff.images_changed
    )
    has_addition = bool(
        diff.text_added or diff.links_added or diff.sections_added or diff.images_added
    )
    if has_removal_or_change or not has_addition:
        return None
    detail = f"{len(diff.text_added)} blok teks baru ditemukan pada halaman ini."
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=url,
        alert_type=ALERT_TYPE_NEW_CONTENT,
        severity=SEVERITY_INFO,
        title="Konten baru terdeteksi",
        detail=detail,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 7. Sitemap tidak dapat diakses -> 'warning'
# --------------------------------------------------------------------------- #
def alert_for_sitemap_unreachable(
    website_id: str,
    reason: Optional[str] = None,
    triggered_at: Optional[datetime] = None,
) -> Alert:
    """Bangun Alert 'warning' untuk sitemap yang tidak dapat diakses.

    Alert ini bersifat level-website (bukan level-halaman), sehingga ``url``
    Alert ditinggalkan ``None``.
    """
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=None,
        alert_type=ALERT_TYPE_SITEMAP_UNREACHABLE,
        severity=SEVERITY_WARNING,
        title="Sitemap tidak dapat diakses",
        detail=reason,
        triggered_at=_now_if_none(triggered_at),
    )


# --------------------------------------------------------------------------- #
# 8. SSL akan kadaluarsa -> 'warning' (<30 hari) | 'critical' (<7 hari)
# --------------------------------------------------------------------------- #
def alert_for_ssl_expiring(
    website_id: str,
    domain: str,
    expires_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[Alert]:
    """Bangun Alert SSL bila sertifikat akan/​telah kadaluarsa.

    - ``expires_at is None`` -> tidak ada info kadaluarsa (mis. cek SSL
      gagal) -> ``None`` (tidak membangkitkan Alert; kegagalan cek SSL bukan
      indikasi SSL bermasalah).
    - Sisa hari ``< SSL_EXPIRY_CRITICAL_DAYS`` (7) -> severity 'critical'.
    - Sisa hari ``< SSL_EXPIRY_WARNING_DAYS`` (30) -> severity 'warning'.
    - Selain itu (masih jauh dari kadaluarsa) -> ``None``.

    Sisa hari dapat bernilai negatif (sertifikat SUDAH kadaluarsa); kasus ini
    tetap dianggap 'critical'.
    """
    if expires_at is None:
        return None
    current = now if now is not None else datetime.now()
    days_left = (expires_at - current).total_seconds() / 86400.0

    if days_left < SSL_EXPIRY_CRITICAL_DAYS:
        severity = SEVERITY_CRITICAL
    elif days_left < SSL_EXPIRY_WARNING_DAYS:
        severity = SEVERITY_WARNING
    else:
        return None

    rounded_days = int(days_left)
    if rounded_days < 0:
        detail = f"Sertifikat SSL untuk {domain} telah kadaluarsa."
    else:
        detail = (
            f"Sertifikat SSL untuk {domain} akan kadaluarsa dalam "
            f"{rounded_days} hari ({expires_at.isoformat()})."
        )
    return Alert(
        id=_new_id(),
        website_id=website_id,
        url=None,
        alert_type=ALERT_TYPE_SSL_EXPIRING,
        severity=severity,
        title="Sertifikat SSL akan kadaluarsa",
        detail=detail,
        triggered_at=_now_if_none(current),
    )


# --------------------------------------------------------------------------- #
# Gabungan: alert per-halaman dari sebuah Diff (dipakai CheckOrchestrator)
# --------------------------------------------------------------------------- #
def generate_page_alerts(
    website_id: str,
    url: str,
    diff: Diff,
    previous_block_count: int,
    triggered_at: Optional[datetime] = None,
) -> List[Alert]:
    """Gabungkan detektor #2-#6 (berbasis Diff) untuk satu halaman.

    Praktis dipakai oleh ``CheckOrchestrator`` setelah ``detect_changes``
    melaporkan perubahan pada sebuah halaman: mengembalikan seluruh Alert
    yang relevan (bisa kosong bila tidak ada kondisi yang terpenuhi).
    """
    alerts: List[Alert] = []
    resolved_time = _now_if_none(triggered_at)

    removal_alert = alert_for_significant_removal(
        website_id, url, diff, previous_block_count, triggered_at=resolved_time
    )
    if removal_alert is not None:
        alerts.append(removal_alert)

    title_alert = alert_for_title_changed(website_id, url, diff, triggered_at=resolved_time)
    if title_alert is not None:
        alerts.append(title_alert)

    meta_alert = alert_for_meta_changed(website_id, url, diff, triggered_at=resolved_time)
    if meta_alert is not None:
        alerts.append(meta_alert)

    heading_alert = alert_for_heading_structure_changed(
        website_id, url, diff, triggered_at=resolved_time
    )
    if heading_alert is not None:
        alerts.append(heading_alert)

    new_content_alert = alert_for_new_content(
        website_id, url, diff, triggered_at=resolved_time
    )
    if new_content_alert is not None:
        alerts.append(new_content_alert)

    return alerts
