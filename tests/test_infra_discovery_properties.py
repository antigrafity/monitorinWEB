"""Property-based tests untuk pembatas Page_Discovery (task 10).

Menguji Correctness Property 6 & 7 pada design memakai Hypothesis (minimal 100
iterasi per properti) terhadap fungsi murni ``crawl_link_graph`` yang memusatkan
logika pembatas kedalaman dan jumlah URL:

- Property 6 (Req 2.2): untuk graf link internal apa pun, penelusuran dari
  homepage tidak pernah menyertakan URL yang berada pada kedalaman lebih dari
  ``max_depth`` tingkat dari homepage.
- Property 7 (Req 2.6): untuk sumber URL sebanyak apa pun, jumlah URL yang
  dikumpulkan tidak pernah melebihi ``max_urls``.

``crawl_link_graph`` menerima ``max_depth`` & ``max_urls`` sebagai parameter
sehingga uji dapat memakai nilai kecil agar cepat; ``discover_pages`` sendiri
memanggilnya dengan batas produksi (5 dan 5000).

Generator menyusun graf link pada satu host (``example.com``) dengan sejumlah
simpul dan tepi acak, sehingga ruang input relevan (banyak simpul, banyak tepi,
duplikat, dan tepi ke luar host) tereksplorasi.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.urls import is_same_host, normalize_url
from monitoring.infra.discovery import crawl_link_graph

BASE_HOST = "example.com"
START = "https://example.com/"


def _node(i: int) -> str:
    """URL simpul internal ternormalisasi ke-``i`` pada BASE_HOST."""
    return normalize_url("https://example.com/p" + str(i))


@st.composite
def _link_graph(draw):
    """Hasilkan (graf link, daftar simpul) pada satu host.

    Simpul: homepage (START) + sejumlah halaman internal ``/pN``. Setiap simpul
    memetakan ke subset acak simpul lain (tepi internal) ditambah kadang tepi ke
    domain eksternal (harus diabaikan oleh crawler).
    """
    n = draw(st.integers(min_value=0, max_value=12))
    nodes = [START] + [_node(i) for i in range(n)]

    graph: Dict[str, List[str]] = {}
    for node in nodes:
        targets = draw(
            st.lists(st.sampled_from(nodes), min_size=0, max_size=len(nodes))
        )
        # Sisipkan sesekali tepi eksternal & subdomain agar filter host teruji.
        extras = draw(
            st.lists(
                st.sampled_from(
                    [
                        "https://other.com/x",
                        "https://sub.example.com/y",
                        "https://example.org/z",
                    ]
                ),
                min_size=0,
                max_size=3,
            )
        )
        graph[node] = targets + extras
    return graph, nodes


def _shortest_depths(start: str, graph: Dict[str, List[str]]) -> Dict[str, int]:
    """Hitung kedalaman BFS terpendek tiap simpul host-sama dari ``start``."""
    depths = {normalize_url(start): 0}
    q = deque([normalize_url(start)])
    while q:
        u = q.popleft()
        for link in graph.get(u, []):
            candidate = normalize_url(link)
            if not is_same_host(candidate, BASE_HOST):
                continue
            if candidate not in depths:
                depths[candidate] = depths[u] + 1
                q.append(candidate)
    return depths


# --------------------------------------------------------------------------- #
# Property 6: Batas kedalaman crawl
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 6: Batas kedalaman crawl
@settings(max_examples=200)
@given(_link_graph(), st.integers(min_value=0, max_value=6))
def test_crawl_never_exceeds_max_depth(graph_and_nodes, max_depth):
    """Validates: Requirements 2.2

    Untuk graf link internal apa pun, hasil ``crawl_link_graph`` tidak pernah
    memuat URL yang kedalaman terpendeknya dari homepage melebihi ``max_depth``.
    Batas jumlah dibuat besar agar tidak mengganggu properti kedalaman.
    """
    graph, _nodes = graph_and_nodes
    result = crawl_link_graph(
        START, graph, BASE_HOST, max_depth=max_depth, max_urls=10_000
    )

    depths = _shortest_depths(START, graph)
    for url in result:
        # Setiap URL yang disertakan dapat dijangkau dan tidak lebih dalam dari batas.
        assert url in depths
        assert depths[url] <= max_depth

    # Semua URL yang disertakan berada pada host yang sama persis (Req 2.3).
    for url in result:
        assert is_same_host(url, BASE_HOST)


# --------------------------------------------------------------------------- #
# Property 7: Batas jumlah URL per website
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 7: Batas jumlah URL per website
@settings(max_examples=200)
@given(_link_graph(), st.integers(min_value=1, max_value=8))
def test_crawl_never_exceeds_max_urls(graph_and_nodes, max_urls):
    """Validates: Requirements 2.6

    Untuk graf dengan sumber URL sebanyak apa pun, jumlah URL yang dikumpulkan
    ``crawl_link_graph`` tidak pernah melebihi ``max_urls`` (di sini dipakai
    nilai kecil agar cepat; produksi memakai 5000). Hasil juga bebas duplikat.
    """
    graph, _nodes = graph_and_nodes
    result = crawl_link_graph(
        START, graph, BASE_HOST, max_depth=10, max_urls=max_urls
    )

    assert len(result) <= max_urls
    # Bebas duplikat (Req 2.4).
    assert len(result) == len(set(result))
