"""Test validasi rentang Polling_Interval (task 3.2).

Berisi:

- Property-based test (Hypothesis, min 100 iterasi) yang memvalidasi
  Property 3 pada design: validasi menerima nilai jika dan hanya jika ia berupa
  bilangan bulat dalam rentang yang diperbolehkan (10..86400 detik untuk
  interval khusus per-website; 1..1440 menit untuk interval global), dan
  menolak nilai non-integer atau di luar rentang.
- Generator yang memproduksi nilai dalam rentang, di luar rentang, dan
  non-integer (float, string, None, bool).
- Unit test contoh spesifik & edge-case (batas rentang, bool, float bulat).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.config import (
    POLL_INTERVAL_MINUTES_ERROR,
    POLL_INTERVAL_SECONDS_ERROR,
    POLL_MAX_MINUTES,
    POLL_MAX_SECONDS,
    POLL_MIN_MINUTES,
    POLL_MIN_SECONDS,
    validate_poll_interval_minutes,
    validate_poll_interval_seconds,
)


# --------------------------------------------------------------------------- #
# Oracle referensi (independen dari implementasi).
# --------------------------------------------------------------------------- #
def _is_valid_int_in_range(value: object, lo: int, hi: int) -> bool:
    """True hanya bila ``value`` bilangan bulat sejati (bukan bool) dalam [lo, hi]."""
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return lo <= value <= hi


# --------------------------------------------------------------------------- #
# Generator: nilai dalam rentang, di luar rentang, dan non-integer.
# --------------------------------------------------------------------------- #
def _interval_values(lo: int, hi: int):
    """Campuran integer dalam/luar rentang + non-integer (float, str, None, bool)."""
    in_range = st.integers(min_value=lo, max_value=hi)
    below_range = st.integers(max_value=lo - 1)
    above_range = st.integers(min_value=hi + 1)
    # Non-integer: float (termasuk yang bernilai bulat seperti 30.0), string,
    # None, dan bool (yang secara teknis subclass int namun harus ditolak).
    non_integers = st.one_of(
        st.floats(allow_nan=False, allow_infinity=False),
        st.floats(min_value=float(lo), max_value=float(hi)),
        st.text(max_size=10),
        st.none(),
        st.booleans(),
    )
    return st.one_of(in_range, below_range, above_range, non_integers)


_seconds_values = _interval_values(POLL_MIN_SECONDS, POLL_MAX_SECONDS)
_minutes_values = _interval_values(POLL_MIN_MINUTES, POLL_MAX_MINUTES)


# --------------------------------------------------------------------------- #
# Property 3: Validasi rentang Polling_Interval
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 3: Validasi rentang Polling_Interval
@settings(max_examples=300)
@given(_seconds_values)
def test_validate_poll_interval_seconds_accepts_iff_int_in_range(value):
    """Validates: Requirements 1.9, 8.5, 8.6

    ``validate_poll_interval_seconds`` menerima nilai jika dan hanya jika ia
    bilangan bulat sejati dalam rentang 10..86400 detik; selain itu ditolak
    dengan pesan kesalahan (pemanggil mempertahankan nilai sebelumnya).
    """
    result = validate_poll_interval_seconds(value)
    expected_ok = _is_valid_int_in_range(value, POLL_MIN_SECONDS, POLL_MAX_SECONDS)

    assert result.ok is expected_ok
    if expected_ok:
        assert result.error_message is None
    else:
        assert result.error_message == POLL_INTERVAL_SECONDS_ERROR


# Feature: website-monitoring, Property 3: Validasi rentang Polling_Interval
@settings(max_examples=300)
@given(_minutes_values)
def test_validate_poll_interval_minutes_accepts_iff_int_in_range(value):
    """Validates: Requirements 1.9, 8.5, 8.6

    ``validate_poll_interval_minutes`` menerima nilai jika dan hanya jika ia
    bilangan bulat sejati dalam rentang 1..1440 menit; selain itu ditolak
    dengan pesan kesalahan (pemanggil mempertahankan nilai sebelumnya).
    """
    result = validate_poll_interval_minutes(value)
    expected_ok = _is_valid_int_in_range(value, POLL_MIN_MINUTES, POLL_MAX_MINUTES)

    assert result.ok is expected_ok
    if expected_ok:
        assert result.error_message is None
    else:
        assert result.error_message == POLL_INTERVAL_MINUTES_ERROR


# --------------------------------------------------------------------------- #
# Unit test contoh spesifik & edge-case.
# --------------------------------------------------------------------------- #
def test_seconds_accepts_boundaries():
    assert validate_poll_interval_seconds(POLL_MIN_SECONDS).ok is True
    assert validate_poll_interval_seconds(POLL_MAX_SECONDS).ok is True
    assert validate_poll_interval_seconds(1800).ok is True


def test_seconds_rejects_out_of_range():
    assert validate_poll_interval_seconds(POLL_MIN_SECONDS - 1).ok is False
    assert validate_poll_interval_seconds(POLL_MAX_SECONDS + 1).ok is False
    assert validate_poll_interval_seconds(0).ok is False
    assert validate_poll_interval_seconds(-5).ok is False


def test_minutes_accepts_boundaries():
    assert validate_poll_interval_minutes(POLL_MIN_MINUTES).ok is True
    assert validate_poll_interval_minutes(POLL_MAX_MINUTES).ok is True
    assert validate_poll_interval_minutes(30).ok is True


def test_minutes_rejects_out_of_range():
    assert validate_poll_interval_minutes(POLL_MIN_MINUTES - 1).ok is False
    assert validate_poll_interval_minutes(POLL_MAX_MINUTES + 1).ok is False
    assert validate_poll_interval_minutes(0).ok is False
    assert validate_poll_interval_minutes(-1).ok is False


def test_rejects_bool_even_though_int_subclass():
    # bool adalah subclass int; True==1, False==0, keduanya harus ditolak.
    assert validate_poll_interval_seconds(True).ok is False
    assert validate_poll_interval_seconds(False).ok is False
    assert validate_poll_interval_minutes(True).ok is False
    assert validate_poll_interval_minutes(False).ok is False


def test_rejects_float_even_whole_number():
    for bad in [30.0, 100.5, 10.0, 1.0]:
        assert validate_poll_interval_seconds(bad).ok is False
        assert validate_poll_interval_minutes(bad).ok is False


def test_rejects_string_and_none():
    for bad in ["30", "abc", "", None]:
        assert validate_poll_interval_seconds(bad).ok is False
        assert validate_poll_interval_minutes(bad).ok is False


def test_rejection_returns_error_message():
    r_sec = validate_poll_interval_seconds(0)
    assert r_sec.ok is False
    assert r_sec.error_message == POLL_INTERVAL_SECONDS_ERROR

    r_min = validate_poll_interval_minutes(0)
    assert r_min.ok is False
    assert r_min.error_message == POLL_INTERVAL_MINUTES_ERROR
