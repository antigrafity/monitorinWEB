/* JavaScript Web Dashboard Monitoring_System (vanilla, tanpa framework).
 *
 * Tanggung jawab:
 * 1. Tombol "Cek Sekarang": POST /websites/{id}/check via fetch(), menampilkan
 *    spinner + teks "Memeriksa…" selama proses, lalu merender ringkasan hasil
 *    secara inline. Menangani 409 (sedang diperiksa), 503 (orchestrator tidak
 *    tersedia), dan 500 (pemeriksaan gagal) dengan pesan yang terbaca, serta
 *    SELALU mengaktifkan kembali tombol (Req 8.4).
 * 2. Konfirmasi sebelum menghapus Monitored_Website (Req 1.2).
 * 3. Fallback anggun untuk thumbnail gambar yang gagal dimuat pada halaman
 *    detail perubahan (Req 10.5, 7.x).
 */
(function () {
    "use strict";

    /** Susun teks ringkasan hasil pemeriksaan dari payload JSON. */
    function summarize(payload) {
        var parts = [];
        parts.push((payload.pages_checked || 0) + " halaman");
        parts.push((payload.changes_detected || 0) + " perubahan");
        if (payload.notifications_sent) {
            parts.push(payload.notifications_sent + " notifikasi");
        }
        if (payload.page_failures) {
            parts.push(payload.page_failures + " halaman gagal");
        }
        return "Selesai: " + parts.join(", ");
    }

    /** Pesan kesalahan yang terbaca untuk tiap kode status yang mungkin. */
    function errorMessage(status, payload) {
        if (status === 409) {
            return "Website ini sedang diperiksa; tunggu hingga selesai.";
        }
        if (status === 503) {
            return "Pemeriksaan manual tidak tersedia (orchestrator tidak aktif).";
        }
        if (status === 404) {
            return "Website tidak ditemukan; muat ulang halaman.";
        }
        if (payload && payload.error) {
            return "Gagal: " + payload.error;
        }
        return "Gagal memeriksa (kode " + status + ").";
    }

    function setResult(node, text, kind) {
        if (!node) {
            return;
        }
        node.textContent = text;
        node.className = "check-result" + (kind ? " " + kind : "");
    }

    function runCheck(button) {
        var websiteId = button.getAttribute("data-website-id");
        var url = button.getAttribute("data-check-url") ||
            "/websites/" + encodeURIComponent(websiteId) + "/check";
        var result = document.querySelector(
            '[data-check-result="' + websiteId + '"]'
        );
        var original = button.innerHTML;

        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.innerHTML = '<span class="spinner" aria-hidden="true"></span> Memeriksa…';
        setResult(result, "Memeriksa…", "");

        function restore() {
            button.disabled = false;
            button.removeAttribute("aria-busy");
            button.innerHTML = original;
        }

        fetch(url, {
            method: "POST",
            headers: { Accept: "application/json" },
        })
            .then(function (response) {
                return response
                    .json()
                    .catch(function () {
                        return null;
                    })
                    .then(function (payload) {
                        return { status: response.status, ok: response.ok, payload: payload };
                    });
            })
            .then(function (res) {
                restore();
                if (res.ok && res.payload && res.payload.ok) {
                    setResult(result, summarize(res.payload), "ok");
                    // Muat ulang agar status & waktu pemeriksaan terbaru tampil.
                    window.setTimeout(function () {
                        window.location.reload();
                    }, 1200);
                } else {
                    setResult(result, errorMessage(res.status, res.payload), "err");
                }
            })
            .catch(function (err) {
                restore();
                setResult(
                    result,
                    "Gagal menghubungi server: " + (err && err.message ? err.message : err),
                    "err"
                );
            });
    }

    document.addEventListener("click", function (event) {
        var checkBtn = event.target.closest
            ? event.target.closest("[data-check-url], .check-btn")
            : null;
        if (checkBtn && !checkBtn.disabled) {
            event.preventDefault();
            runCheck(checkBtn);
        }
    });

    // Konfirmasi penghapusan (Req 1.2).
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (form && form.hasAttribute && form.hasAttribute("data-confirm")) {
            if (!window.confirm(form.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        }
    });

    // Fallback thumbnail gambar yang gagal dimuat (tetap tampilkan URL-nya).
    document.addEventListener(
        "error",
        function (event) {
            var node = event.target;
            if (node && node.tagName === "IMG" && node.classList.contains("thumb")) {
                var item = node.closest ? node.closest(".image-item") : null;
                if (item) {
                    item.classList.add("thumb-failed");
                }
            }
        },
        true
    );

    // --- Modal popup (mis. "Tambah Website") -------------------------- //
    // Tombol dengan [data-open-modal="<id>"] membuka overlay #<id>; tombol/
    // elemen dengan [data-close-modal] di dalamnya menutupnya. Overlay juga
    // ditutup lewat klik di luar panel atau tombol Escape.
    function openModal(modal) {
        if (!modal) { return; }
        modal.hidden = false;
        document.body.classList.add("modal-open");
        var firstInput = modal.querySelector("input, select, textarea");
        if (firstInput) { firstInput.focus(); }
    }

    function closeModal(modal) {
        if (!modal) { return; }
        modal.hidden = true;
        document.body.classList.remove("modal-open");
    }

    document.addEventListener("click", function (event) {
        var opener = event.target.closest ? event.target.closest("[data-open-modal]") : null;
        if (opener) {
            event.preventDefault();
            openModal(document.getElementById(opener.getAttribute("data-open-modal")));
            return;
        }
        var closer = event.target.closest ? event.target.closest("[data-close-modal]") : null;
        if (closer) {
            event.preventDefault();
            closeModal(closer.closest(".modal-overlay"));
            return;
        }
        // Klik di luar panel (langsung pada overlay) menutup modal.
        if (event.target.classList && event.target.classList.contains("modal-overlay")) {
            closeModal(event.target);
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            document.querySelectorAll(".modal-overlay:not([hidden])").forEach(closeModal);
        }
    });

    // Buka modal otomatis bila URL menyertakan hash yang cocok dengan id-nya
    // (mis. tautan "+ Add Website" dari halaman Overview: /websites#add-website-modal)
    // atau bila server menandai [data-open-on-load] (ada pesan kesalahan dari
    // percobaan submit sebelumnya).
    document.querySelectorAll(".modal-overlay").forEach(function (modal) {
        if (modal.hasAttribute("data-open-on-load") ||
            (window.location.hash && window.location.hash === "#" + modal.id)) {
            openModal(modal);
        }
    });

    // Tahap 4: tombol "Kirim tes notifikasi" pada Settings > Notifications.
    var testNotifBtn = document.getElementById("btn-test-notification");
    if (testNotifBtn) {
        testNotifBtn.addEventListener("click", function () {
            var resultEl = document.getElementById("test-notification-result");
            testNotifBtn.disabled = true;
            testNotifBtn.textContent = "Mengirim…";
            if (resultEl) { resultEl.textContent = ""; resultEl.className = "test-result"; }

            fetch("/settings/test-notification", { method: "POST" })
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    testNotifBtn.disabled = false;
                    testNotifBtn.textContent = "Kirim Tes Notifikasi";
                    if (resultEl) {
                        if (data.ok) {
                            resultEl.textContent = "✓ Notifikasi tes berhasil dikirim!";
                            resultEl.className = "test-result test-result-ok";
                        } else {
                            resultEl.textContent = "✗ Gagal: " + (data.error || "Tidak diketahui");
                            resultEl.className = "test-result test-result-err";
                        }
                    }
                })
                .catch(function (err) {
                    testNotifBtn.disabled = false;
                    testNotifBtn.textContent = "Kirim Tes Notifikasi";
                    if (resultEl) {
                        resultEl.textContent = "✗ Gagal menghubungi server.";
                        resultEl.className = "test-result test-result-err";
                    }
                });
        });
    }
})();
