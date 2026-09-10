"""Smoke test untuk struktur proyek dan konstanta config (Task 1).

Memastikan package dapat diimpor dan konstanta konfigurasi bernilai sesuai
design (Req 1.5, 8.3).
"""

import importlib


def test_packages_importable():
    for name in (
        "monitoring",
        "monitoring.config",
        "monitoring.domain",
        "monitoring.infra",
        "monitoring.app",
        "monitoring.web",
    ):
        assert importlib.import_module(name) is not None


def test_config_constants():
    from monitoring import config

    assert config.POLL_MIN_SECONDS == 10
    assert config.POLL_MAX_SECONDS == 86400
    assert config.POLL_MIN_MINUTES == 1
    assert config.POLL_MAX_MINUTES == 1440
    assert config.DEFAULT_GLOBAL_INTERVAL_SECONDS == 21600  # Req 8.3: 6 jam
    assert config.MAX_WEBSITES == 100  # Req 1.5, 1.8
    assert config.DOMAIN_MAX_LEN == 253
