"""Chart SVG buatan sendiri untuk Web Dashboard (ContentMonitor Tahap 1).

Modul ini menyediakan fungsi MURNI (tanpa I/O) yang menghasilkan markup SVG
sebagai string untuk:

- ``line_chart_svg``: chart "Content Changes Over Time" (7 hari terakhir) —
  garis dengan titik data, grid halus, label tanggal, dan area gradien di
  bawah garis.
- ``sparkline_svg``: sparkline kecil pada kolom "Changes (7D)" tabel website.
- ``avatar_style``: huruf depan + warna latar deterministik dari sebuah
  domain, dipakai sebagai badge avatar pada tabel/panel.

Seluruhnya ditulis tangan (tanpa library chart/CDN eksternal) agar dashboard
tetap bekerja offline (Req desain umum: tanpa dependensi berat). Setiap fungsi
menangani kasus SEMUA nilai nol tanpa membagi dengan nol.

Kompatibel Python 3.9 (``from __future__ import annotations`` + ``typing``).
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from markupsafe import Markup

# Palet warna indigo/violet untuk avatar (aksen ContentMonitor).
_AVATAR_PALETTE = (
    "#4f46e5",  # indigo-600
    "#7c3aed",  # violet-600
    "#6366f1",  # indigo-500
    "#8b5cf6",  # violet-500
    "#4338ca",  # indigo-700
    "#5b21b6",  # violet-800
    "#6d28d9",  # violet-700
    "#3730a3",  # indigo-800
)


def avatar_style(seed: str) -> Tuple[str, str]:
    """Kembalikan ``(huruf_depan, warna_hex)`` deterministik dari ``seed``.

    ``seed`` biasanya berupa domain atau nama website. Warna dipilih dari
    palet indigo/violet berdasarkan hash Python bawaan; hasilnya stabil untuk
    proses yang sama (Python mengacak seed hash antar-proses secara bawaan,
    namun karena kita hanya butuh KONSISTENSI dalam satu render/permintaan,
    bukan lintas proses/waktu, ini dapat diterima. Untuk kestabilan lintas
    proses dipakai checksum karakter sederhana, bukan ``hash()`` bawaan).
    """
    text = (seed or "?").strip()
    letter = text[0].upper() if text else "?"
    checksum = sum(ord(ch) for ch in text) if text else 0
    color = _AVATAR_PALETTE[checksum % len(_AVATAR_PALETTE)]
    return letter, color


def _safe_max(values: Sequence[int]) -> int:
    """Nilai maksimum yang aman dipakai sebagai penyebut skala (>= 1)."""
    m = max(values) if values else 0
    return m if m > 0 else 1


def sparkline_svg(
    counts: Sequence[int],
    width: int = 120,
    height: int = 32,
    color: str = "#6366f1",
) -> Markup:
    """Hasilkan sparkline SVG kecil dari daftar hitungan harian.

    Menangani daftar kosong maupun seluruh nilai nol dengan menggambar garis
    datar di dasar chart (tidak pernah membagi dengan nol).
    """
    values = list(counts)
    if not values:
        # Tidak ada data sama sekali -> garis datar kosong.
        svg = (
            '<svg class="sparkline" width="{w}" height="{h}" '
            'viewBox="0 0 {w} {h}" role="img" aria-label="Tidak ada data">'
            '<line x1="0" y1="{h_half}" x2="{w}" y2="{h_half}" '
            'stroke="var(--border-strong)" stroke-width="1.5" stroke-dasharray="2,2"/>'
            "</svg>"
        ).format(w=width, h=height, h_half=height / 2)
        return Markup(svg)

    n = len(values)
    vmax = _safe_max(values)
    pad = 3
    usable_w = max(width - 2 * pad, 1)
    usable_h = max(height - 2 * pad, 1)

    if n == 1:
        xs = [width / 2]
    else:
        step = usable_w / (n - 1)
        xs = [pad + i * step for i in range(n)]

    ys = [pad + usable_h - (v / vmax) * usable_h for v in values]

    points = " ".join(
        "{0:.1f},{1:.1f}".format(x, y) for x, y in zip(xs, ys)
    )

    total = sum(values)
    if total == 0:
        # Semua nol -> garis datar di dasar (tidak pecah, tetap terlihat rapi).
        line_y = pad + usable_h
        svg = (
            '<svg class="sparkline" width="{w}" height="{h}" '
            'viewBox="0 0 {w} {h}" role="img" aria-label="Tidak ada perubahan">'
            '<line x1="{pad}" y1="{y}" x2="{right}" y2="{y}" '
            'stroke="var(--border-strong)" stroke-width="1.5"/>'
            "</svg>"
        ).format(w=width, h=height, pad=pad, right=width - pad, y=line_y)
        return Markup(svg)

    svg = (
        '<svg class="sparkline" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}" role="img" aria-label="Tren perubahan 7 hari terakhir">'
        '<polyline points="{pts}" fill="none" stroke="{color}" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    ).format(w=width, h=height, pts=points, color=color)
    return Markup(svg)


def line_chart_svg(
    labels: Sequence[str],
    counts: Sequence[int],
    width: int = 640,
    height: int = 220,
    color: str = "#6366f1",
) -> Markup:
    """Hasilkan line chart SVG "Content Changes Over Time" (7 hari terakhir).

    Menyertakan grid horizontal halus, titik data, label tanggal pada sumbu-x,
    dan area gradien di bawah garis. Menangani kasus SEMUA nilai nol dengan
    menggambar garis datar di dasar chart beserta grid, tanpa membagi dengan
    nol (skala memakai penyebut minimum 1).
    """
    values = list(counts)
    n = len(values)
    if n == 0:
        return Markup(
            '<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            'role="img" aria-label="Tidak ada data perubahan"></svg>'.format(
                w=width, h=height
            )
        )

    pad_left, pad_right, pad_top, pad_bottom = 36, 16, 16, 28
    usable_w = max(width - pad_left - pad_right, 1)
    usable_h = max(height - pad_top - pad_bottom, 1)

    vmax = _safe_max(values)
    # Bulatkan skala maksimum ke atas agar grid rapi (mis. 1,2,5,10,20,50,...).
    grid_max = _nice_ceiling(vmax)

    if n == 1:
        xs = [pad_left + usable_w / 2]
    else:
        step = usable_w / (n - 1)
        xs = [pad_left + i * step for i in range(n)]
    ys = [
        pad_top + usable_h - (v / grid_max) * usable_h if grid_max else pad_top + usable_h
        for v in values
    ]

    points = " ".join("{0:.1f},{1:.1f}".format(x, y) for x, y in zip(xs, ys))

    # Area gradien di bawah garis: tutup poligon ke dasar chart.
    baseline = pad_top + usable_h
    area_points = (
        "{0:.1f},{1:.1f} ".format(xs[0], baseline)
        + points
        + " {0:.1f},{1:.1f}".format(xs[-1], baseline)
    )

    # Grid horizontal halus (4 garis termasuk dasar).
    grid_lines = []
    grid_labels = []
    grid_steps = 4
    for i in range(grid_steps + 1):
        gy = pad_top + usable_h - (usable_h * i / grid_steps)
        grid_lines.append(
            '<line x1="{x1}" y1="{y:.1f}" x2="{x2}" y2="{y:.1f}" '
            'stroke="var(--border)" stroke-width="1"/>'.format(
                x1=pad_left, x2=width - pad_right, y=gy
            )
        )
        label_value = round(grid_max * i / grid_steps)
        grid_labels.append(
            '<text x="{x}" y="{y:.1f}" class="chart-axis-label" '
            'text-anchor="end" dy="0.32em">{v}</text>'.format(
                x=pad_left - 6, y=gy, v=label_value
            )
        )

    # Titik data + label tanggal (sumbu-x). Label dipendekkan "DD/MM".
    dots = []
    x_labels = []
    for x, y, label in zip(xs, ys, labels):
        dots.append(
            '<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" '
            'stroke="var(--surface)" stroke-width="1.5"/>'.format(
                x=x, y=y, color=color
            )
        )
        short_label = _short_date_label(label)
        x_labels.append(
            '<text x="{x:.1f}" y="{y}" class="chart-axis-label" '
            'text-anchor="middle">{lbl}</text>'.format(
                x=x, y=height - 6, lbl=short_label
            )
        )

    gradient_id = "changesGradient"
    svg = (
        '<svg class="line-chart" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}" role="img" '
        'aria-label="Grafik perubahan konten 7 hari terakhir" '
        'preserveAspectRatio="xMidYMid meet">'
        "<defs>"
        '<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="{color}" stop-opacity="0.28"/>'
        '<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        "</linearGradient>"
        "</defs>"
        "{grid}"
        '<polygon points="{area_points}" fill="url(#{gid})"/>'
        '<polyline points="{points}" fill="none" stroke="{color}" '
        'stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>'
        "{dots}"
        "{grid_labels}"
        "{x_labels}"
        "</svg>"
    ).format(
        w=width,
        h=height,
        gid=gradient_id,
        color=color,
        grid="".join(grid_lines),
        area_points=area_points,
        points=points,
        dots="".join(dots),
        grid_labels="".join(grid_labels),
        x_labels="".join(x_labels),
    )
    return Markup(svg)


def _nice_ceiling(value: int) -> int:
    """Bulatkan ``value`` ke atas menjadi angka "rapi" untuk skala grid.

    Contoh: 0/1 -> 1, 3 -> 4 (tangga 1/2/4/5/10 * 10^n), 47 -> 50. Selalu
    mengembalikan nilai >= 1 sehingga aman dipakai sebagai penyebut skala.
    """
    if value <= 1:
        return 1
    import math

    magnitude = 10 ** int(math.floor(math.log10(value)))
    for step in (1, 2, 4, 5, 10):
        candidate = step * magnitude
        if candidate >= value:
            return candidate
    return 10 * magnitude


def donut_chart_svg(
    added: int,
    removed: int,
    updated: int,
    size: int = 180,
    stroke_width: int = 28,
) -> Markup:
    """Hasilkan donut chart SVG "Changes by Type" (Added/Removed/Updated).

    Ditulis tangan sebagai tiga arc lingkaran (``stroke-dasharray``/
    ``stroke-dashoffset`` pada elemen ``<circle>``) tanpa library chart/CDN
    eksternal (ContentMonitor Tahap 5). Total ditampilkan di tengah donut.

    Menangani kasus TOTAL = 0 (``added == removed == updated == 0``) dengan
    menggambar cincin abu-abu polos beserta angka "0" di tengah -- TIDAK
    pernah membagi dengan nol.
    """
    total = added + removed + updated
    radius = (size - stroke_width) / 2
    center = size / 2
    circumference = 2 * 3.14159265358979 * radius

    if total <= 0:
        svg = (
            '<svg class="donut-chart" width="{sz}" height="{sz}" '
            'viewBox="0 0 {sz} {sz}" role="img" '
            'aria-label="Tidak ada perubahan pada rentang ini">'
            '<circle cx="{c}" cy="{c}" r="{r}" fill="none" '
            'stroke="var(--border)" stroke-width="{sw}"/>'
            '<text x="{c}" y="{c}" text-anchor="middle" dominant-baseline="middle" '
            'class="donut-center-value">0</text>'
            "</svg>"
        ).format(sz=size, c=center, r=radius, sw=stroke_width)
        return Markup(svg)

    segments = (
        ("added", added, "#2e9e63"),
        ("removed", removed, "#d24a41"),
        ("updated", updated, "#d69b1f"),
    )

    arcs = []
    offset = 0.0
    # Mulai dari jam 12 (atas) dengan rotasi -90 derajat pada grup <g>.
    for _label, count, color in segments:
        if count <= 0:
            continue
        fraction = count / total
        arc_length = fraction * circumference
        gap = circumference - arc_length
        arcs.append(
            '<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{color}" '
            'stroke-width="{sw}" stroke-dasharray="{arc:.2f} {gap:.2f}" '
            'stroke-dashoffset="-{off:.2f}" stroke-linecap="butt"/>'.format(
                c=center,
                r=radius,
                color=color,
                sw=stroke_width,
                arc=arc_length,
                gap=gap,
                off=offset,
            )
        )
        offset += arc_length

    svg = (
        '<svg class="donut-chart" width="{sz}" height="{sz}" '
        'viewBox="0 0 {sz} {sz}" role="img" '
        'aria-label="Distribusi tipe perubahan: {added} ditambahkan, '
        '{removed} dihapus, {updated} diperbarui">'
        '<g transform="rotate(-90 {c} {c})">{arcs}</g>'
        '<text x="{c}" y="{c}" text-anchor="middle" dominant-baseline="middle" '
        'class="donut-center-value">{total}</text>'
        "</svg>"
    ).format(
        sz=size,
        c=center,
        arcs="".join(arcs),
        total=total,
        added=added,
        removed=removed,
        updated=updated,
    )
    return Markup(svg)


def _short_date_label(iso_date: str) -> str:
    """Ubah "YYYY-MM-DD" menjadi label pendek "DD/MM" untuk sumbu-x chart."""
    try:
        parts = iso_date.split("-")
        if len(parts) == 3:
            return "{0}/{1}".format(parts[2], parts[1])
    except Exception:
        pass
    return iso_date
