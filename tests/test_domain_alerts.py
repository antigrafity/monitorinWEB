"""Unit & property test untuk pembangkit Alert (ContentMonitor Tahap 3).

Menguji seluruh fungsi murni di ``monitoring.domain.alerts``:

- Halaman gagal diakses -> 'critical' (dengan/tanpa kode status).
- Ambang penghapusan konten signifikan (unit + property-based test).
- Judul/meta berubah -> 'info', menyertakan nilai lama & baru.
- Struktur heading berubah -> 'info'.
- Konten baru (hanya penambahan) -> 'info'.
- Sitemap tidak dapat diakses -> 'warning'.
- SSL akan kadaluarsa -> 'warning' (<30 hari) / 'critical' (<7 hari).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.config import SIGNIFICANT_REMOVAL_BLOCKS, SIGNIFICANT_REMOVAL_RATIO
from monitoring.domain.alerts import (
    alert_for_heading_structure_changed,
    alert_for_meta_changed,
    alert_for_new_content,
    alert_for_page_unreachable,
    alert_for_significant_removal,
    alert_for_sitemap_unreachable,
    alert_for_ssl_expiring,
    alert_for_title_changed,
    generate_page_alerts,
    is_significant_removal,
)
from monitoring.domain.models import Diff

NOW = datetime(2024, 1, 1, 12, 0, 0)


# --------------------------------------------------------------------------- #
# 1. Halaman gagal diakses -> 'critical'
# --------------------------------------------------------------------------- #
def test_page_unreachable_with_status_code_includes_code_in_title():
    alert = alert_for_page_unreachable(
        "w1", "https://a.com/x", status_code=404, reason="not found", triggered_at=NOW
    )
    assert alert.severity == "critical"
    assert "404" in alert.title
    assert alert.website_id == "w1"
    assert alert.url == "https://a.com/x"
    assert alert.detail == "not found"
    assert alert.status == "unread"
    assert alert.triggered_at == NOW


def test_page_unreachable_without_status_code_has_generic_title():
    alert = alert_for_page_unreachable(
        "w1", "https://a.com/x", status_code=None, reason="timeout"
    )
    assert alert.severity == "critical"
    assert alert.title == "Halaman tidak dapat diakses"


# --------------------------------------------------------------------------- #
# 2. Penghapusan konten signifikan -> 'warning'
# --------------------------------------------------------------------------- #
def test_is_significant_removal_absolute_threshold():
    assert is_significant_removal(SIGNIFICANT_REMOVAL_BLOCKS, 100) is True
    assert is_significant_removal(SIGNIFICANT_REMOVAL_BLOCKS - 1, 1000) is False


def test_is_significant_removal_ratio_threshold():
    # 50% dari 4 blok = 2 blok dihapus -> signifikan meski di bawah ambang absolut.
    assert is_significant_removal(2, 4) is True
    assert is_significant_removal(1, 4) is False


def test_is_significant_removal_zero_previous_blocks_only_absolute():
    # previous_block_count <= 0 -> hanya ambang absolut berlaku (hindari div/0).
    assert is_significant_removal(0, 0) is False
    assert is_significant_removal(SIGNIFICANT_REMOVAL_BLOCKS, 0) is True


def test_alert_for_significant_removal_none_when_not_significant():
    diff = Diff(text_removed=["satu blok"])
    alert = alert_for_significant_removal("w1", "https://a.com/", diff, 100)
    assert alert is None


def test_alert_for_significant_removal_warning_when_significant():
    diff = Diff(text_removed=[f"blok {i}" for i in range(SIGNIFICANT_REMOVAL_BLOCKS)])
    alert = alert_for_significant_removal(
        "w1", "https://a.com/", diff, 100, triggered_at=NOW
    )
    assert alert is not None
    assert alert.severity == "warning"
    assert alert.title == "Penghapusan konten signifikan"
    assert str(SIGNIFICANT_REMOVAL_BLOCKS) in alert.detail


# Feature: contentmonitor, Property: ambang alert penghapusan
@settings(max_examples=150)
@given(
    removed_count=st.integers(min_value=0, max_value=200),
    previous_block_count=st.integers(min_value=0, max_value=200),
)
def test_significant_removal_property(removed_count, previous_block_count):
    """Validates: ambang penghapusan konten signifikan (Tahap 3, bagian B.2)

    Properti: ``is_significant_removal`` mengembalikan True jika dan hanya
    jika jumlah blok dihapus >= SIGNIFICANT_REMOVAL_BLOCKS ATAU (bila
    previous_block_count > 0) proporsi blok dihapus >= SIGNIFICANT_REMOVAL_RATIO.
    ``alert_for_significant_removal`` menghasilkan Alert (bukan None) jika dan
    hanya jika kondisi ini terpenuhi, dan sebaliknya None.
    """
    expected = removed_count >= SIGNIFICANT_REMOVAL_BLOCKS or (
        previous_block_count > 0
        and (removed_count / previous_block_count) >= SIGNIFICANT_REMOVAL_RATIO
    )
    actual = is_significant_removal(removed_count, previous_block_count)
    assert actual == expected

    diff = Diff(text_removed=[f"blok {i}" for i in range(removed_count)])
    alert = alert_for_significant_removal(
        "w1", "https://a.com/", diff, previous_block_count
    )
    assert (alert is not None) == expected
    if alert is not None:
        assert alert.severity == "warning"


# --------------------------------------------------------------------------- #
# 3 & 4. Judul & meta description berubah -> 'info'
# --------------------------------------------------------------------------- #
def test_alert_for_title_changed_none_when_unchanged():
    diff = Diff()
    assert alert_for_title_changed("w1", "https://a.com/", diff) is None


def test_alert_for_title_changed_info_with_old_and_new():
    diff = Diff(title_changed=("Judul Lama", "Judul Baru"))
    alert = alert_for_title_changed("w1", "https://a.com/", diff, triggered_at=NOW)
    assert alert is not None
    assert alert.severity == "info"
    assert "Judul Lama" in alert.detail
    assert "Judul Baru" in alert.detail


def test_alert_for_meta_changed_none_when_unchanged():
    diff = Diff()
    assert alert_for_meta_changed("w1", "https://a.com/", diff) is None


def test_alert_for_meta_changed_info_with_old_and_new():
    diff = Diff(meta_changed=("Meta lama", "Meta baru"))
    alert = alert_for_meta_changed("w1", "https://a.com/", diff)
    assert alert is not None
    assert alert.severity == "info"
    assert "Meta lama" in alert.detail
    assert "Meta baru" in alert.detail


# --------------------------------------------------------------------------- #
# 5. Perubahan struktur heading -> 'info'
# --------------------------------------------------------------------------- #
def test_alert_for_heading_structure_changed_none_when_no_section_changes():
    diff = Diff()
    assert alert_for_heading_structure_changed("w1", "https://a.com/", diff) is None


def test_alert_for_heading_structure_changed_info_when_sections_added_or_removed():
    diff = Diff(sections_added=["Section Baru"], sections_removed=["Section Lama"])
    alert = alert_for_heading_structure_changed("w1", "https://a.com/", diff)
    assert alert is not None
    assert alert.severity == "info"
    assert "ditambahkan" in alert.detail
    assert "dihapus" in alert.detail


# --------------------------------------------------------------------------- #
# 6. Konten baru (hanya penambahan) -> 'info'
# --------------------------------------------------------------------------- #
def test_alert_for_new_content_none_when_no_additions():
    diff = Diff()
    assert alert_for_new_content("w1", "https://a.com/", diff) is None


def test_alert_for_new_content_none_when_mixed_addition_and_removal():
    diff = Diff(text_added=["baru"], text_removed=["lama"])
    assert alert_for_new_content("w1", "https://a.com/", diff) is None


def test_alert_for_new_content_info_when_only_additions():
    diff = Diff(text_added=["baru 1", "baru 2"])
    alert = alert_for_new_content("w1", "https://a.com/", diff)
    assert alert is not None
    assert alert.severity == "info"
    assert alert.title == "Konten baru terdeteksi"


# --------------------------------------------------------------------------- #
# 7. Sitemap tidak dapat diakses -> 'warning'
# --------------------------------------------------------------------------- #
def test_alert_for_sitemap_unreachable_is_warning_without_url():
    alert = alert_for_sitemap_unreachable("w1", reason="gagal fetch", triggered_at=NOW)
    assert alert.severity == "warning"
    assert alert.url is None
    assert alert.website_id == "w1"


# --------------------------------------------------------------------------- #
# 8. SSL akan kadaluarsa -> 'warning' (<30 hari) / 'critical' (<7 hari)
# --------------------------------------------------------------------------- #
def test_ssl_alert_none_when_no_expiry_info():
    assert alert_for_ssl_expiring("w1", "a.com", None, now=NOW) is None


def test_ssl_alert_none_when_far_from_expiry():
    expires_at = NOW + timedelta(days=60)
    assert alert_for_ssl_expiring("w1", "a.com", expires_at, now=NOW) is None


def test_ssl_alert_warning_when_under_30_days():
    expires_at = NOW + timedelta(days=20)
    alert = alert_for_ssl_expiring("w1", "a.com", expires_at, now=NOW)
    assert alert is not None
    assert alert.severity == "warning"


def test_ssl_alert_critical_when_under_7_days():
    expires_at = NOW + timedelta(days=3)
    alert = alert_for_ssl_expiring("w1", "a.com", expires_at, now=NOW)
    assert alert is not None
    assert alert.severity == "critical"


def test_ssl_alert_critical_when_already_expired():
    expires_at = NOW - timedelta(days=1)
    alert = alert_for_ssl_expiring("w1", "a.com", expires_at, now=NOW)
    assert alert is not None
    assert alert.severity == "critical"
    assert "kadaluarsa" in alert.detail.lower()


# --------------------------------------------------------------------------- #
# generate_page_alerts: gabungan detektor berbasis Diff
# --------------------------------------------------------------------------- #
def test_generate_page_alerts_combines_multiple_sources():
    diff = Diff(
        text_removed=[f"blok {i}" for i in range(SIGNIFICANT_REMOVAL_BLOCKS)],
        title_changed=("Lama", "Baru"),
        meta_changed=("Meta lama", "Meta baru"),
        sections_added=["Baru"],
    )
    alerts = generate_page_alerts("w1", "https://a.com/", diff, 100, triggered_at=NOW)
    types = {a.alert_type for a in alerts}
    assert "significant_removal" in types
    assert "title_changed" in types
    assert "meta_changed" in types
    assert "heading_structure_changed" in types


def test_generate_page_alerts_empty_when_no_conditions_met():
    diff = Diff(text_added=["x"], text_removed=["y"])  # campuran, tidak signifikan
    alerts = generate_page_alerts("w1", "https://a.com/", diff, 100)
    assert alerts == []
