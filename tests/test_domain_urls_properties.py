"""Property-based tests untuk util URL Page_Discovery (task 7.2).

Menguji Correctness Property 4 dan 5 pada design memakai Hypothesis (minimal
100 iterasi per properti):

- Property 4 (Req 2.3): ``is_same_host`` hanya menerima URL yang host-nya sama
  persis (case-insensitive) dengan base host; subdomain berbeda maupun domain
  eksternal ditolak.
- Property 5 (Req 2.4): ``dedup_urls`` menghasilkan daftar tanpa duplikat yang
  mempertahankan urutan kemunculan pertama, dan bersifat idempoten.

Generator dirancang cerdas agar terkonsentrasi pada ruang input yang relevan:
Property 4 menarik kandidat dari pool yang mencakup host dasar (berbagai casing),
subdomain-nya, dan host eksternal; Property 5 menarik daftar dari pool kecil URL
sehingga duplikat sering muncul.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.urls import dedup_urls, is_same_host

# --------------------------------------------------------------------------- #
# Strategi generator
# --------------------------------------------------------------------------- #

# Label domain sederhana (huruf/angka) untuk menyusun host valid.
_LABEL = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=6)


@st.composite
def _base_host(draw):
    """Host dasar berupa dua/tiga label dipisah titik, mis. ``example.com``."""
    n = draw(st.integers(min_value=2, max_value=3))
    labels = [draw(_LABEL) for _ in range(n)]
    return ".".join(labels)


def _randomize_case(draw, text: str) -> str:
    """Ubah casing tiap karakter secara acak untuk menguji case-insensitivity."""
    flags = draw(st.lists(st.booleans(), min_size=len(text), max_size=len(text)))
    return "".join(c.upper() if f else c.lower() for c, f in zip(text, flags))


@st.composite
def _candidates_and_base(draw):
    """Hasilkan (base_host, daftar URL kandidat) dari pool campuran.

    Pool kandidat mencakup:
    - URL pada host dasar yang sama (casing acak) -> harus diterima.
    - URL pada subdomain dari host dasar -> harus ditolak.
    - URL pada domain eksternal -> harus ditolak.
    - URL relatif tanpa host -> harus ditolak.
    """
    base = draw(_base_host())
    sub_label = draw(_LABEL)
    external = draw(_base_host().filter(lambda h: h != base))
    path = draw(st.sampled_from(["", "/", "/page", "/a/b", "/x?q=1"]))

    same_host_url = "https://" + _randomize_case(draw, base) + path
    subdomain_url = "https://" + sub_label + "." + base + path
    external_url = "https://" + external + path
    relative_url = path or "/rel"

    pool = [
        (same_host_url, True),
        (subdomain_url, False),
        (external_url, False),
        (relative_url, False),
    ]
    candidates = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=8))
    return base, candidates


# Pool kecil URL agar duplikat sering muncul dalam daftar yang dihasilkan.
_URL_POOL = [
    "https://a.com/1",
    "https://a.com/2",
    "https://a.com/3",
    "https://b.com/",
    "https://b.com/x#frag",
    "relative/path",
    "",
]
_URL_LISTS = st.lists(st.sampled_from(_URL_POOL), max_size=20)


# --------------------------------------------------------------------------- #
# Property 4: Penemuan halaman terbatas pada host yang sama
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 4: Penemuan halaman terbatas pada host yang sama
@settings(max_examples=200)
@given(_candidates_and_base())
def test_is_same_host_accepts_only_exact_host(data):
    """Validates: Requirements 2.3

    ``is_same_host`` mengembalikan ``True`` tepat untuk URL yang host-nya sama
    persis (case-insensitive) dengan base host, dan ``False`` untuk subdomain
    berbeda, domain eksternal, maupun URL relatif. Memfilter daftar kandidat
    dengan ``is_same_host`` menghasilkan hanya URL host-sama.
    """
    base, candidates = data

    for url, expected in candidates:
        assert is_same_host(url, base) is expected

    # Hasil penyaringan hanya berisi URL yang host-nya sama persis (lowercase).
    kept = [url for url, _ in candidates if is_same_host(url, base)]
    for url in kept:
        assert (urlsplit(url).hostname or "").lower() == base.lower()


# --------------------------------------------------------------------------- #
# Property 5: Deduplikasi URL
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 5: Deduplikasi URL
@settings(max_examples=200)
@given(_URL_LISTS)
def test_dedup_urls_unique_order_preserving_idempotent(urls):
    """Validates: Requirements 2.4

    ``dedup_urls`` menghasilkan daftar tanpa duplikat, mempertahankan urutan
    kemunculan pertama, memuat himpunan elemen yang sama dengan masukan, dan
    bersifat idempoten (menerapkannya dua kali sama dengan sekali).
    """
    result = dedup_urls(urls)

    # 1. Tanpa duplikat.
    assert len(result) == len(set(result))

    # 2. Himpunan elemen dipertahankan.
    assert set(result) == set(urls)

    # 3. Urutan sesuai kemunculan pertama pada masukan.
    first_occurrence = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            first_occurrence.append(url)
    assert result == first_occurrence

    # 4. Idempoten.
    assert dedup_urls(result) == result
