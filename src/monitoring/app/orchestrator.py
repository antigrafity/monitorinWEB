"""CheckOrchestrator — orkestrasi satu siklus pemeriksaan website (task 12).

Merangkai pipeline lengkap untuk SATU Monitored_Website sesuai bagian
*Application Layer / CheckOrchestrator* pada design:

    discovery -> fetch halaman/gambar -> normalize -> hash -> bangun Snapshot
    -> detect_changes -> simpan Snapshot + Change_Event -> notifikasi (hanya
    bila berubah) -> update last_checked/status.

Prinsip isolasi kegagalan (Req 3.3, 5.5, 7.5, 7.6):

- Setiap halaman diproses sebagai unit kerja terpisah dan dijalankan bersama
  memakai ``asyncio.gather(..., return_exceptions=True)`` sehingga kegagalan
  (exception) pada satu halaman tidak membatalkan pemrosesan halaman lain.
- Kegagalan fetch sebuah halaman ditandai, Snapshot sebelumnya dipertahankan
  (halaman tidak menimpa baseline), dan siklus berlanjut (Req 3.3).
- Setiap gambar pada halaman juga diproses dengan ``asyncio.gather(...,
  return_exceptions=True)``; kegagalan unduh (Req 7.5) atau kegagalan
  perhitungan Image_Hash (Req 7.6) melewati gambar tersebut tanpa menghentikan
  gambar lain maupun halaman.

Kebijakan pelaporan hanya-saat-berubah (Req 9.5, 12.1, 12.2, 12.3, 12.4):

- Notifikasi HANYA dipicu ketika ``detect_changes`` melaporkan perubahan dan
  Change_Event berhasil disimpan (Req 12.2). Saat tidak ada perubahan, tidak
  ada Change_Event maupun notifikasi (Req 9.5, 12.1).
- Bila penyimpanan Change_Event gagal, baseline dipertahankan, notifikasi
  TIDAK dipicu untuk event tersebut, dan ``last_checked`` TIDAK diperbarui ke
  "success" untuk siklus ini (Req 12.4).

Presisi diff teks & section (Req 6.2, 6.4)
------------------------------------------
Teks halaman diekstrak per blok (``extract_text_blocks``) lalu digabung dengan
newline sebelum disimpan sebagai ``Snapshot.normalized_text``; ``content_hash``
dihitung dari teks gabungan yang sama. Karena Change_Detector membandingkan
teks baris-per-baris, diff menunjuk paragraf/heading/item daftar yang benar-benar
berubah alih-alih melaporkan seluruh teks halaman tergantikan.

Blok section (``split_sections`` -> ``Section.as_block()``) ikut dipersist pada
``Snapshot.sections``, sehingga ``previous_sections`` dapat diambil dari
Snapshot sebelumnya dan diff section bermakna antar pemeriksaan. Pada baseline
(belum ada Snapshot sebelumnya) ``previous_sections`` bernilai ``None``, yang
diperlakukan ``detect_changes`` sebagai kumpulan kosong.

Pemeliharaan Data_Store (Req 5.4, 11.4)
---------------------------------------
Setelah Snapshot sebuah halaman berhasil disimpan, ``prune_snapshots(url)``
dipanggil sehingga hanya ``SNAPSHOT_RETENTION_PER_URL`` Snapshot terbaru per
halaman yang dipertahankan. Snapshot terbaru (baseline perbandingan) selalu
dipertahankan dan riwayat Change_Event tidak pernah disentuh. Kegagalan
pemangkasan bersifat non-fatal: siklus pemeriksaan tetap berjalan.

Jeda per halaman & pencatatan kegagalan (ContentMonitor Tahap 2)
------------------------------------------------------------------
Sebelum sebuah halaman diproses, ``page_state.paused`` diperiksa lewat
``repository.get_page_states``; URL yang dijeda DILEWATI sepenuhnya (tidak
di-fetch, tidak menghasilkan outcome apa pun) sehingga tidak ikut dihitung
sebagai halaman diperiksa/gagal pada siklus tersebut. Untuk halaman yang
GAGAL diambil, alasannya dicatat ke ``page_state`` via
``repository.record_page_error`` (dipakai halaman Pages untuk status
"Broken"); untuk halaman yang BERHASIL diambil, catatan kegagalan sebelumnya
dibersihkan via ``repository.clear_page_error``. Kedua pemanggilan dibungkus
try/except terpisah — kegagalan mencatat/membersihkan status halaman TIDAK
BOLEH menghentikan siklus pemeriksaan, konsisten dengan pola pemeliharaan
Data_Store non-fatal lain di modul ini.

Alert & tracking title/meta/SSL (ContentMonitor Tahap 3)
----------------------------------------------------------
Setelah memproses sebuah halaman, Alert dibangun dari kondisi yang terjadi
(halaman gagal diakses, penghapusan konten signifikan, judul/meta berubah,
struktur heading berubah, konten baru) via ``domain.alerts`` lalu disimpan;
kegagalan menyimpan Alert TIDAK menghentikan siklus. Sitemap tak terjangkau
(dilaporkan oleh ``discover_pages``) dan SSL akan kadaluarsa (dicek SEKALI per
website via ``ssl_checker`` yang di-inject — bukan bawaan, sehingga tidak
menyentuh jaringan kecuali disuntikkan secara eksplisit, lihat ``main.py``)
juga menjadi sumber Alert level-website. Seluruh Alert 'critical' pada satu
siklus dirangkum menjadi SATU pesan Telegram ringkas (``notifier.notify_text``)
di akhir ``check_website``, bukan satu pesan per Alert (mencegah spam).
``CheckResult.alerts_created`` mengagregasi jumlah Alert yang berhasil
tersimpan pada siklus tersebut.

Kompatibel Python 3.9 melalui ``from __future__ import annotations`` dan
``typing``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..domain.alerts import (
    alert_for_page_unreachable,
    alert_for_sitemap_unreachable,
    alert_for_ssl_expiring,
    generate_page_alerts,
)
from ..domain.detector import detect_changes
from ..domain.hashing import content_hash, image_hash
from ..domain.keywords import match_keywords
from ..domain.models import Alert, Keyword, Snapshot, WebsiteConfig
from ..domain.normalizer import (
    extract_image_urls,
    extract_links,
    extract_meta_description,
    extract_text_blocks,
    extract_title,
    split_sections,
)
from ..infra.discovery import discover_pages
from ..infra.fetcher import Fetcher
from ..infra.notifier import Notifier, format_critical_alerts_message
from ..infra.repository import Repository


@dataclass
class CheckResult:
    """Ringkasan hasil satu siklus pemeriksaan sebuah Monitored_Website.

    Dipakai oleh Scheduler/pengujian untuk mengetahui apa yang terjadi tanpa
    harus membaca ulang Data_Store.

    Attributes:
        website_id: ID Monitored_Website yang diperiksa.
        status: Status akhir siklus: ``"success"``, ``"failure"`` (discovery /
            homepage gagal), atau ``"incomplete"`` (ada kegagalan pembuatan
            Change_Event sehingga last_checked sengaja tidak diperbarui ke
            success — Req 12.4).
        pages_checked: Jumlah halaman yang berhasil diambil & diproses.
        changes_detected: Jumlah halaman yang perubahannya terdeteksi.
        notifications_sent: Jumlah notifikasi yang berhasil dikirim.
        page_failures: Jumlah halaman yang gagal diambil (Req 3.3).
        image_failures: Jumlah gambar yang gagal diunduh/di-hash (Req 7.5, 7.6).
        snapshot_save_failures: Jumlah kegagalan penyimpanan Snapshot (Req 5.5).
        event_save_failures: Jumlah kegagalan penyimpanan Change_Event (Req 12.4).
        last_check_updated: ``True`` bila ``update_last_check`` dipanggil pada
            siklus ini.
        discovery_failed: ``True`` bila penemuan halaman gagal (Req 2.7).
        errors: Daftar pesan kesalahan yang terkumpul selama siklus.
    """

    website_id: str
    status: str = "success"
    pages_checked: int = 0
    changes_detected: int = 0
    notifications_sent: int = 0
    page_failures: int = 0
    image_failures: int = 0
    snapshot_save_failures: int = 0
    event_save_failures: int = 0
    last_check_updated: bool = False
    discovery_failed: bool = False
    # Jumlah Alert yang berhasil disimpan pada siklus ini (ContentMonitor
    # Tahap 3, bagian E).
    alerts_created: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class _PageOutcome:
    """Hasil pemrosesan satu halaman (internal, diagregasi oleh check_website)."""

    url: str
    fetched: bool = False
    snapshot_saved: bool = False
    changed: bool = False
    # None: tidak ada Change_Event yang perlu disimpan; True/False: hasil simpan.
    event_saved: Optional[bool] = None
    notified: bool = False
    image_failures: int = 0
    error: Optional[str] = None
    # Jumlah Alert yang berhasil disimpan untuk halaman ini (Tahap 3).
    alerts_created: int = 0
    # Alert 'critical' (bila ada) untuk dirangkum ke satu notifikasi per
    # siklus per website (Tahap 3, bagian E).
    critical_alerts: List[Alert] = field(default_factory=list)


class CheckOrchestrator:
    """Merangkai pipeline pemeriksaan untuk satu Monitored_Website.

    Dependensi di-inject agar mudah diuji: ``repository`` (Data_Store),
    ``fetcher`` (HTTP), ``notifier`` (Telegram), dan ``now`` (callable penyedia
    waktu, bawaan :func:`datetime.now`) untuk timestamp Snapshot yang
    deterministik pada pengujian.
    """

    def __init__(
        self,
        repository: Repository,
        fetcher: Fetcher,
        notifier: Notifier,
        now: Optional[Callable[[], datetime]] = None,
        ssl_checker: Optional[
            Callable[[str], Awaitable[Optional[datetime]]]
        ] = None,
    ) -> None:
        self._repository = repository
        self._fetcher = fetcher
        self._notifier = notifier
        self._now = now if now is not None else (lambda: datetime.now())
        # Cek SSL (Tahap 3, sumber Alert #8) bersifat OPT-IN via injeksi: bila
        # ``None`` (bawaan), cek SSL DILEWATI sepenuhnya sehingga pengujian
        # yang tidak menyediakannya tidak pernah menyentuh jaringan nyata.
        # Wiring produksi (main.py) menyuntikkan
        # ``monitoring.infra.ssl_check.check_ssl_expiry`` secara eksplisit.
        self._ssl_checker = ssl_checker

    async def check_website(self, website: WebsiteConfig) -> CheckResult:
        """Jalankan satu siklus pemeriksaan penuh untuk ``website``.

        Alur:

        1. Temukan halaman (``discover_pages``). Bila gagal (mis. homepage tak
           dapat diakses, Req 2.7), tandai pemeriksaan gagal, setel
           ``last_status='failure'``, dan hentikan siklus.
        2. Proses seluruh halaman secara konkuren dengan
           ``asyncio.gather(..., return_exceptions=True)`` sehingga kegagalan
           satu halaman tidak membatalkan yang lain (Req 3.3).
        3. Perbarui ``last_checked``/status di akhir sesuai kebijakan pelaporan
           hanya-saat-berubah (Req 12.1, 12.4).

        Returns:
            :class:`CheckResult` yang meringkas hasil siklus.
        """
        result = CheckResult(website_id=website.id)
        # Alert 'critical' yang berhasil disimpan pada siklus ini, dirangkum
        # menjadi SATU pesan Telegram ringkas di akhir (Tahap 3, bagian E;
        # "jangan spam" — bukan satu pesan per Alert).
        critical_alerts: List[Alert] = []

        discovery = await discover_pages(website, self._fetcher)

        # Tahap 3 (sumber Alert #7): sitemap tidak dapat diakses -> bangun &
        # simpan Alert 'warning' level-website. Kegagalan menyimpan Alert
        # tidak boleh menghentikan siklus (dibungkus try/except terpisah).
        if getattr(discovery, "sitemap_unreachable", False):
            try:
                sitemap_alert = alert_for_sitemap_unreachable(
                    website.id,
                    reason="Seluruh kandidat path sitemap gagal diambil.",
                    triggered_at=self._now(),
                )
                if await self._repository.save_alert(sitemap_alert):
                    result.alerts_created += 1
            except Exception:
                pass

        if discovery.failed:
            # Req 2.7: homepage/discovery gagal -> pemeriksaan gagal, jangan
            # ubah daftar/baseline tersimpan. Tandai status gagal.
            result.discovery_failed = True
            result.status = "failure"
            if discovery.failure_reason:
                result.errors.append(discovery.failure_reason)
            await self._repository.update_last_check(
                website.id, self._now(), "failure"
            )
            result.last_check_updated = True
            return result

        # Tahap 3 (sumber Alert #8): cek SSL SEKALI per website per siklus
        # (bukan per halaman) — HANYA bila ``ssl_checker`` disuntikkan (bawaan
        # ``None`` -> dilewati sepenuhnya, sehingga pengujian yang tidak
        # menyediakannya tidak pernah menyentuh jaringan nyata; lihat main.py
        # untuk wiring produksi). Kegagalan cek SSL -> None -> tidak
        # membangkitkan Alert (lihat alert_for_ssl_expiring). Kegagalan
        # menyimpan Alert tidak boleh menghentikan siklus.
        if self._ssl_checker is not None:
            try:
                expires_at = await self._ssl_checker(website.domain)
                ssl_alert = alert_for_ssl_expiring(
                    website.id, website.domain, expires_at, now=self._now()
                )
                if ssl_alert is not None:
                    if await self._repository.save_alert(ssl_alert):
                        result.alerts_created += 1
                        if ssl_alert.severity == "critical":
                            critical_alerts.append(ssl_alert)
            except Exception:
                pass

        # Lewati URL yang dijeda secara individual (Tahap 2): tidak di-fetch,
        # tidak ikut diagregasi sebagai pages_checked/page_failures. Kegagalan
        # membaca status jeda tidak boleh menghentikan siklus — bila gagal,
        # anggap tidak ada halaman yang dijeda (fail-open pada level *baca*,
        # aman karena hanya mempengaruhi apakah halaman diperiksa lebih awal,
        # bukan integritas data).
        try:
            paused_states = await self._repository.get_page_states(
                website_id=website.id
            )
            paused_urls = {ps.url for ps in paused_states if ps.paused}
        except Exception:
            paused_urls = set()
        urls_to_process = [u for u in discovery.urls if u not in paused_urls]

        # Proses tiap halaman secara konkuren; isolasi kegagalan per halaman.
        outcomes = await asyncio.gather(
            *(self._process_page(website, url) for url in urls_to_process),
            return_exceptions=True,
        )

        for url, outcome in zip(urls_to_process, outcomes):
            if isinstance(outcome, BaseException):
                # Exception tak terduga pada satu halaman tidak menghentikan
                # siklus (Req 3.3); catat dan lanjut.
                result.page_failures += 1
                result.errors.append(
                    f"Kegagalan tak terduga saat memproses {url}: {outcome!r}"
                )
                continue
            self._aggregate(result, outcome)
            critical_alerts.extend(outcome.critical_alerts)

        # Kebijakan update last_checked (Req 12.1, 12.4).
        if result.event_save_failures > 0:
            # Req 12.4: pembuatan Change_Event gagal -> pertahankan baseline &
            # JANGAN perbarui last_checked ke success.
            result.status = "incomplete"
            result.last_check_updated = False
        else:
            await self._repository.update_last_check(
                website.id, self._now(), "success"
            )
            result.status = "success"
            result.last_check_updated = True

        # Tahap 3 (bagian E): kirim SATU pesan Telegram ringkas yang merangkum
        # seluruh Alert 'critical' pada siklus ini (bila ada) — bukan satu
        # pesan per Alert, agar tidak spam. Kegagalan pengiriman tidak boleh
        # menghentikan/menggagalkan siklus.
        if critical_alerts:
            try:
                message = format_critical_alerts_message(critical_alerts, website)
                if message:
                    await self._notifier_notify_text(message)
            except Exception:
                pass

        return result

    def _aggregate(self, result: CheckResult, outcome: _PageOutcome) -> None:
        """Gabungkan hasil satu halaman ke ringkasan siklus."""
        if outcome.fetched:
            result.pages_checked += 1
        else:
            result.page_failures += 1
        if outcome.changed:
            result.changes_detected += 1
        if outcome.event_saved is False:
            result.event_save_failures += 1
        if not outcome.snapshot_saved and outcome.fetched:
            result.snapshot_save_failures += 1
        if outcome.notified:
            result.notifications_sent += 1
        result.image_failures += outcome.image_failures
        result.alerts_created += outcome.alerts_created
        if outcome.error:
            result.errors.append(outcome.error)

    async def _process_page(
        self, website: WebsiteConfig, url: str
    ) -> _PageOutcome:
        """Proses satu halaman: fetch -> normalize -> hash -> detect -> persist.

        Kegagalan fetch halaman (Req 3.3) mengembalikan outcome ``fetched=False``
        tanpa menimpa Snapshot sebelumnya. Selain itu, halaman diproses penuh
        dan hasilnya dikembalikan untuk diagregasi.
        """
        outcome = _PageOutcome(url=url)

        fetch = await self._fetcher.fetch_page(url)
        if not fetch.ok or fetch.html is None:
            # Req 3.3: halaman gagal diambil -> pertahankan Snapshot sebelumnya,
            # lanjut halaman lain. Tidak membangun/menyimpan Snapshot baru.
            reason = fetch.failure_reason or "penyebab tidak diketahui"
            outcome.error = f"Gagal mengambil halaman {url}: {reason}"
            # Tahap 2: catat kegagalan untuk status "Broken" pada halaman
            # Pages. Kegagalan menyimpan catatan ini tidak boleh menghentikan
            # siklus pemeriksaan (Req desain umum ketahanan pemeliharaan).
            try:
                await self._repository.record_page_error(
                    url, website.id, reason
                )
            except Exception:
                pass
            # Tahap 3 (sumber Alert #1): halaman gagal diakses -> Alert
            # 'critical'. Kegagalan menyimpan Alert tidak boleh menghentikan
            # siklus.
            try:
                page_alert = alert_for_page_unreachable(
                    website.id,
                    url,
                    status_code=fetch.status_code,
                    reason=reason,
                    triggered_at=self._now(),
                )
                if await self._repository.save_alert(page_alert):
                    outcome.alerts_created += 1
                    outcome.critical_alerts.append(page_alert)
            except Exception:
                pass
            return outcome

        outcome.fetched = True
        html = fetch.html

        # Tahap 2: halaman berhasil diambil -> bersihkan catatan kegagalan
        # sebelumnya (bila ada). Non-fatal bila gagal dibersihkan.
        try:
            await self._repository.clear_page_error(url)
        except Exception:
            pass

        # Normalisasi teks per blok (paragraf/heading/item) lalu gabungkan
        # dengan newline sehingga diff berbasis baris pada Change_Detector
        # menunjuk blok yang berubah, bukan seluruh halaman (Req 6.2).
        # HTML malformed ditoleransi tanpa menghentikan pemrosesan (Req 4.5).
        blocks = extract_text_blocks(html)
        text = "\n".join(blocks)

        links = extract_links(html, url)
        image_urls = extract_image_urls(html, url)
        current_sections = [section.as_block() for section in split_sections(html)]
        # Tahap 3: judul (<title>) & meta description ikut diekstrak dan
        # dipersist bersama Snapshot agar perubahannya dapat dideteksi antar
        # pemeriksaan (fungsi murni, tidak pernah raise pada HTML malformah).
        page_title = extract_title(html)
        page_meta_description = extract_meta_description(html)

        # Unduh & hash gambar dengan isolasi kegagalan per gambar (Req 7.5, 7.6).
        image_hashes, image_failures = await self._process_images(image_urls)
        outcome.image_failures = image_failures

        checked_at = self._now()
        current = Snapshot(
            url=url,
            website_id=website.id,
            normalized_text=text,
            # Hash dihitung dari teks yang SAMA dengan yang dibandingkan agar
            # gerbang hash konsisten dengan diff (Req 6.1).
            content_hash=content_hash(text),
            checked_at=checked_at,
            links=links,
            image_hashes=image_hashes,
            sections=current_sections,
            title=page_title,
            meta_description=page_meta_description,
        )

        previous = await self._repository.get_latest_snapshot(url)

        # Blok section versi sebelumnya diambil dari Snapshot tersimpan sehingga
        # diff section bermakna antar pemeriksaan (Req 6.4). None hanya saat
        # baseline (belum ada Snapshot sebelumnya).
        detection = detect_changes(
            previous,
            current,
            previous_sections=previous.sections if previous is not None else None,
            current_sections=current_sections,
        )

        # Selalu simpan Snapshot baru untuk mempertahankan riwayat (Req 5.4).
        saved = await self._repository.save_snapshot(current)
        outcome.snapshot_saved = saved
        if not saved:
            # Req 5.5: simpan Snapshot gagal -> pertahankan Snapshot sebelumnya,
            # catat indikator gagal, lanjut.
            outcome.error = f"Gagal menyimpan Snapshot untuk {url}"
            return outcome

        # Pemeliharaan Data_Store: sisakan hanya beberapa Snapshot terbaru per
        # halaman agar basis data tidak bertambah tanpa batas (Req 5.4, 11.4).
        # Snapshot terbaru dan seluruh riwayat Change_Event tidak pernah
        # terhapus. Kegagalan pemangkasan tidak boleh menghentikan siklus
        # pemeriksaan, jadi ditoleransi tanpa mengubah hasil halaman.
        try:
            await self._repository.prune_snapshots(url)
        except Exception:
            pass

        # Tahap 4: pencocokan kata kunci. Ambil daftar kata kunci yang
        # didaftarkan untuk website ini, cocokkan terhadap blok teks halaman,
        # dan bangkitkan Alert 'warning' hanya bila STATUS BERUBAH (dedup via
        # keyword_state di repository). Kegagalan pada seluruh langkah ini
        # bersifat non-fatal — siklus tetap berlanjut.
        try:
            kw_list = await self._repository.list_keywords(website.id)
            if kw_list:
                kw_matches = match_keywords(blocks, kw_list)
                for m in kw_matches:
                    kw = m.keyword
                    new_status = "found" if m.found else "missing"
                    old_status = await self._repository.get_keyword_state(
                        website.id, url, kw.id
                    )
                    # Hanya buat Alert bila status berubah (dedup).
                    if old_status != new_status:
                        await self._repository.set_keyword_state(
                            website.id, url, kw.id, new_status
                        )
                        should_alert = (
                            (kw.mode == "must_exist" and new_status == "missing")
                            or (kw.mode == "must_not_exist" and new_status == "found")
                        )
                        if should_alert:
                            kw_title = (
                                f'Kata kunci "{kw.keyword}" tidak ditemukan'
                                if kw.mode == "must_exist"
                                else f'Kata kunci "{kw.keyword}" muncul'
                            )
                            kw_alert = Alert(
                                id=f"kw-{kw.id}-{url}-{checked_at.isoformat()}",
                                website_id=website.id,
                                url=url,
                                alert_type="keyword_" + kw.mode,
                                severity="warning",
                                title=kw_title,
                                detail=f"Mode: {kw.mode}, status: {new_status}",
                                triggered_at=checked_at,
                            )
                            try:
                                if await self._repository.save_alert(kw_alert):
                                    outcome.alerts_created += 1
                            except Exception:
                                pass
        except Exception:
            pass

        # Notifikasi & Change_Event hanya bila terdeteksi perubahan (Req 9.5,
        # 12.1, 12.2).
        if detection.changed and detection.change_event is not None:
            outcome.changed = True
            event_saved = await self._repository.save_change_event(
                detection.change_event
            )
            outcome.event_saved = event_saved
            if not event_saved:
                # Req 12.4: pembuatan Change_Event gagal -> pertahankan baseline,
                # JANGAN picu notifikasi, JANGAN update last_checked ke success.
                outcome.error = f"Gagal menyimpan Change_Event untuk {url}"
                return outcome

            # Req 12.2: picu notifikasi hanya setelah event tersimpan.
            notified = await self._notifier.notify(detection.change_event, website)
            outcome.notified = bool(notified)

            # Tahap 3 (bagian B & E): bangun & simpan Alert dari Diff halaman
            # ini (penghapusan signifikan, title/meta berubah, struktur
            # heading berubah, konten baru). Ambang penghapusan signifikan
            # dihitung terhadap jumlah blok halaman SEBELUMNYA (``blocks`` dari
            # Snapshot sebelumnya bila ada, selain itu 0). Kegagalan menyimpan
            # Alert tidak boleh menghentikan siklus.
            previous_block_count = (
                len(previous.normalized_text.splitlines()) if previous is not None else 0
            )
            page_alerts = generate_page_alerts(
                website.id,
                url,
                detection.diff,
                previous_block_count,
                triggered_at=checked_at,
            )
            for page_alert in page_alerts:
                try:
                    if await self._repository.save_alert(page_alert):
                        outcome.alerts_created += 1
                        if page_alert.severity == "critical":
                            outcome.critical_alerts.append(page_alert)
                except Exception:
                    pass

        return outcome

    async def _process_images(
        self, image_urls: List[str]
    ) -> Tuple[Dict[str, str], int]:
        """Unduh & hash setiap gambar dengan isolasi kegagalan (Req 7.5, 7.6).

        Menjalankan seluruh unduhan bersama via ``asyncio.gather(...,
        return_exceptions=True)``. Gambar yang gagal diunduh (Req 7.5), gagal
        di-hash (Req 7.6), atau memicu exception dilewati tanpa menghentikan
        gambar lain.

        Returns:
            Pasangan ``(image_hashes, failures)`` di mana ``image_hashes`` adalah
            pemetaan ``url -> image_hash`` untuk gambar yang berhasil, dan
            ``failures`` adalah jumlah gambar yang gagal.
        """
        if not image_urls:
            return {}, 0

        results = await asyncio.gather(
            *(self._process_image(image_url) for image_url in image_urls),
            return_exceptions=True,
        )

        image_hashes: Dict[str, str] = {}
        failures = 0
        for res in results:
            if isinstance(res, BaseException) or res is None:
                failures += 1
                continue
            image_url, digest = res
            image_hashes[image_url] = digest
        return image_hashes, failures

    async def _notifier_notify_text(self, message: str) -> None:
        """Kirim teks bebas via notifier bila mendukungnya (Tahap 3, bagian E).

        Notifier tiruan pada pengujian tahap-tahap sebelumnya (mis.
        ``FakeNotifier``/``NullNotifier``) hanya mengimplementasikan
        ``notify(event, website)``, bukan ``notify_text``. Metode ini
        merosot anggun (tidak melakukan apa pun) bila ``notify_text`` tidak
        tersedia, sehingga ketiadaan dukungan ringkasan Alert tidak pernah
        menghentikan siklus pemeriksaan.
        """
        notify_text = getattr(self._notifier, "notify_text", None)
        if notify_text is None:
            return
        await notify_text(message)

    async def _process_image(
        self, image_url: str
    ) -> Optional[Tuple[str, str]]:
        """Unduh satu gambar dan hitung Image_Hash (Req 7.2, 7.5, 7.6).

        Mengembalikan ``(url, image_hash)`` bila berhasil, atau ``None`` bila
        unduhan gagal (Req 7.5) atau perhitungan hash gagal (Req 7.6).
        """
        result = await self._fetcher.fetch_image(image_url)
        if not result.ok or result.content is None:
            # Req 7.5: unduhan gagal -> lewati gambar ini.
            return None
        try:
            digest = image_hash(result.content)
        except Exception:
            # Req 7.6: perhitungan Image_Hash gagal -> lewati gambar ini.
            return None
        return image_url, digest
