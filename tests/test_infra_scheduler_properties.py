"""Property-based test Scheduler (task 13).

Memvalidasi Property 2 pada design: pemilihan Polling_Interval efektif.

Untuk sembarang Monitored_Website dan nilai global, ``effective_interval``
mengembalikan ``poll_interval_seconds`` khusus website bila disetel (dan valid
dalam 10..86400), dan mengembalikan nilai global bila tidak ada interval
khusus.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.config import validate_poll_interval_seconds
from monitoring.domain.models import WebsiteConfig
from monitoring.infra.scheduler import Scheduler


# --------------------------------------------------------------------------- #
# Generator: WebsiteConfig dengan poll_interval_seconds None atau int valid
# (10..86400), serta nilai global arbitrer.
# --------------------------------------------------------------------------- #
@st.composite
def _websites(draw):
    poll = draw(
        st.one_of(
            st.none(),
            st.integers(min_value=10, max_value=86400),
        )
    )
    return WebsiteConfig(
        id=draw(st.text(min_size=1, max_size=12)),
        domain="example.com",
        name="Example",
        poll_interval_seconds=poll,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


_global_values = st.integers(min_value=1, max_value=1_000_000)


def _make_scheduler() -> Scheduler:
    """Scheduler dengan dependensi tak-terpakai untuk uji fungsi murni."""
    return Scheduler(repository=object(), orchestrator=object())


def _expected(website: WebsiteConfig, global_value: int) -> int:
    """Oracle referensi independen dari implementasi."""
    custom = website.poll_interval_seconds
    if custom is not None and validate_poll_interval_seconds(custom).ok:
        return custom
    return global_value


# Feature: website-monitoring, Property 2: Pemilihan Polling_Interval efektif
@settings(max_examples=200)
@given(_websites(), _global_values)
def test_effective_interval_prefers_valid_custom_else_global(website, global_value):
    """Validates: Requirements 1.6, 8.2, 8.3

    ``effective_interval`` mengembalikan ``poll_interval_seconds`` khusus bila
    disetel dan valid (10..86400); selain itu mengembalikan nilai global.
    """
    scheduler = _make_scheduler()
    result = scheduler.effective_interval(website, global_value)

    assert result == _expected(website, global_value)

    if website.poll_interval_seconds is not None:
        # Generator hanya memproduksi nilai valid, jadi pasti dipakai.
        assert result == website.poll_interval_seconds
    else:
        assert result == global_value


# Feature: website-monitoring, Property 2: Pemilihan Polling_Interval efektif
@settings(max_examples=200)
@given(
    st.one_of(
        st.none(),
        st.integers(min_value=-100, max_value=200000),
    ),
    _global_values,
)
def test_effective_interval_falls_back_when_custom_invalid(custom, global_value):
    """Validates: Requirements 1.6, 8.2, 8.3

    Ketika interval khusus tidak valid (di luar 10..86400) atau ``None``,
    ``effective_interval`` jatuh kembali ke nilai global; hanya nilai khusus
    yang valid yang dipakai.
    """
    website = WebsiteConfig(
        id="web-x",
        domain="example.com",
        name="Example",
        poll_interval_seconds=custom,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )
    scheduler = _make_scheduler()
    result = scheduler.effective_interval(website, global_value)

    if custom is not None and validate_poll_interval_seconds(custom).ok:
        assert result == custom
    else:
        assert result == global_value
