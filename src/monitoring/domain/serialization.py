"""Serialisasi JSON untuk model domain (task 2.2).

Menyediakan fungsi serialize/deserialize untuk ``Snapshot``, ``Diff``, dan
``ChangeEvent`` agar dapat disimpan sebagai teks JSON di Data_Store (Req 5.2).

Prinsip desain:

- ``datetime`` diserialkan sebagai string ISO 8601 (``datetime.isoformat()``)
  dan dideserialkan kembali dengan ``datetime.fromisoformat`` sehingga bersifat
  lossless untuk nilai yang tidak menyertakan timezone-name khusus.
- Daftar link kosong dan dict Image_Hash kosong dipertahankan apa adanya
  (``[]`` dan ``{}``), bukan diubah menjadi ``None`` (Req 5.2).
- Fungsi ``*_to_dict``/``*_from_dict`` mengembalikan struktur Python biasa
  (dict/list) yang JSON-serializable; fungsi ``serialize_*``/``deserialize_*``
  membungkusnya dengan ``json.dumps``/``json.loads``.

Target runtime Python 3.9: memakai ``from __future__ import annotations`` dan
tipe dari ``typing``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from monitoring.domain.models import (
    Alert,
    ChangeEvent,
    ChangeSummary,
    Diff,
    Snapshot,
)


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def snapshot_to_dict(snapshot: Snapshot) -> Dict[str, Any]:
    """Ubah ``Snapshot`` menjadi dict JSON-serializable."""
    return {
        "url": snapshot.url,
        "website_id": snapshot.website_id,
        "normalized_text": snapshot.normalized_text,
        "content_hash": snapshot.content_hash,
        "checked_at": snapshot.checked_at.isoformat(),
        "links": list(snapshot.links),
        "image_hashes": dict(snapshot.image_hashes),
        "sections": list(snapshot.sections),
        "title": snapshot.title,
        "meta_description": snapshot.meta_description,
    }


def snapshot_from_dict(data: Dict[str, Any]) -> Snapshot:
    """Bangun kembali ``Snapshot`` dari dict hasil :func:`snapshot_to_dict`.

    ``sections``, ``title``, dan ``meta_description`` bersifat opsional
    (default ``[]``/``None``) sehingga payload lama yang diserialkan sebelum
    bidang-bidang ini ada tetap dapat dimuat (ContentMonitor Tahap 3).
    """
    return Snapshot(
        url=data["url"],
        website_id=data["website_id"],
        normalized_text=data["normalized_text"],
        content_hash=data["content_hash"],
        checked_at=datetime.fromisoformat(data["checked_at"]),
        links=list(data.get("links", [])),
        image_hashes=dict(data.get("image_hashes", {})),
        sections=list(data.get("sections", [])),
        title=data.get("title"),
        meta_description=data.get("meta_description"),
    )


def serialize_snapshot(snapshot: Snapshot) -> str:
    """Serialkan ``Snapshot`` ke string JSON."""
    return json.dumps(snapshot_to_dict(snapshot))


def deserialize_snapshot(payload: str) -> Snapshot:
    """Deserialkan string JSON menjadi ``Snapshot``."""
    return snapshot_from_dict(json.loads(payload))


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #
def _pair_to_list(pair: Optional[Any]) -> Optional[List[str]]:
    """Ubah pasangan ``(lama, baru)`` menjadi list JSON-serializable atau None."""
    if pair is None:
        return None
    old, new = pair
    return [old, new]


def _list_to_pair(value: Optional[Any]) -> Optional[Any]:
    """Bangun kembali pasangan ``(lama, baru)`` dari list hasil serialisasi."""
    if not value:
        return None
    return (value[0], value[1])


def diff_to_dict(diff: Diff) -> Dict[str, Any]:
    """Ubah ``Diff`` menjadi dict JSON-serializable."""
    return {
        "text_added": list(diff.text_added),
        "text_removed": list(diff.text_removed),
        "links_added": list(diff.links_added),
        "links_removed": list(diff.links_removed),
        "sections_added": list(diff.sections_added),
        "sections_removed": list(diff.sections_removed),
        "images_added": list(diff.images_added),
        "images_removed": list(diff.images_removed),
        "images_changed": list(diff.images_changed),
        "title_changed": _pair_to_list(diff.title_changed),
        "meta_changed": _pair_to_list(diff.meta_changed),
    }


def diff_from_dict(data: Dict[str, Any]) -> Diff:
    """Bangun kembali ``Diff`` dari dict hasil :func:`diff_to_dict`.

    ``title_changed``/``meta_changed`` bersifat opsional (default ``None``)
    sehingga payload lama yang diserialkan sebelum bidang ini ada (Tahap 3)
    tetap dapat dideserialkan tanpa error.
    """

    def _lst(key: str) -> List[str]:
        return list(data.get(key, []))

    return Diff(
        text_added=_lst("text_added"),
        text_removed=_lst("text_removed"),
        links_added=_lst("links_added"),
        links_removed=_lst("links_removed"),
        sections_added=_lst("sections_added"),
        sections_removed=_lst("sections_removed"),
        images_added=_lst("images_added"),
        images_removed=_lst("images_removed"),
        images_changed=_lst("images_changed"),
        title_changed=_list_to_pair(data.get("title_changed")),
        meta_changed=_list_to_pair(data.get("meta_changed")),
    )


def serialize_diff(diff: Diff) -> str:
    """Serialkan ``Diff`` ke string JSON."""
    return json.dumps(diff_to_dict(diff))


def deserialize_diff(payload: str) -> Diff:
    """Deserialkan string JSON menjadi ``Diff``."""
    return diff_from_dict(json.loads(payload))


# --------------------------------------------------------------------------- #
# ChangeSummary
# --------------------------------------------------------------------------- #
def summary_to_dict(summary: ChangeSummary) -> Dict[str, Any]:
    """Ubah ``ChangeSummary`` menjadi dict JSON-serializable."""
    return {
        "text_added": summary.text_added,
        "text_removed": summary.text_removed,
        "links_added": summary.links_added,
        "links_removed": summary.links_removed,
        "images_changed": summary.images_changed,
    }


def summary_from_dict(data: Dict[str, Any]) -> ChangeSummary:
    """Bangun kembali ``ChangeSummary`` dari dict hasil :func:`summary_to_dict`."""
    return ChangeSummary(
        text_added=data["text_added"],
        text_removed=data["text_removed"],
        links_added=data["links_added"],
        links_removed=data["links_removed"],
        images_changed=data["images_changed"],
    )


# --------------------------------------------------------------------------- #
# ChangeEvent
# --------------------------------------------------------------------------- #
def change_event_to_dict(event: ChangeEvent) -> Dict[str, Any]:
    """Ubah ``ChangeEvent`` menjadi dict JSON-serializable (termasuk Diff & summary)."""
    return {
        "id": event.id,
        "website_id": event.website_id,
        "url": event.url,
        "detected_at": event.detected_at.isoformat(),
        "diff": diff_to_dict(event.diff),
        "summary": summary_to_dict(event.summary),
    }


def change_event_from_dict(data: Dict[str, Any]) -> ChangeEvent:
    """Bangun kembali ``ChangeEvent`` dari dict hasil :func:`change_event_to_dict`."""
    return ChangeEvent(
        id=data["id"],
        website_id=data["website_id"],
        url=data["url"],
        detected_at=datetime.fromisoformat(data["detected_at"]),
        diff=diff_from_dict(data["diff"]),
        summary=summary_from_dict(data["summary"]),
    )


def serialize_change_event(event: ChangeEvent) -> str:
    """Serialkan ``ChangeEvent`` ke string JSON."""
    return json.dumps(change_event_to_dict(event))


def deserialize_change_event(payload: str) -> ChangeEvent:
    """Deserialkan string JSON menjadi ``ChangeEvent``."""
    return change_event_from_dict(json.loads(payload))


# --------------------------------------------------------------------------- #
# Alert (ContentMonitor Tahap 3)
# --------------------------------------------------------------------------- #
def alert_to_dict(alert: Alert) -> Dict[str, Any]:
    """Ubah ``Alert`` menjadi dict JSON-serializable."""
    return {
        "id": alert.id,
        "website_id": alert.website_id,
        "url": alert.url,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "detail": alert.detail,
        "triggered_at": alert.triggered_at.isoformat(),
        "status": alert.status,
    }


def alert_from_dict(data: Dict[str, Any]) -> Alert:
    """Bangun kembali ``Alert`` dari dict hasil :func:`alert_to_dict`."""
    return Alert(
        id=data["id"],
        website_id=data["website_id"],
        url=data.get("url"),
        alert_type=data["alert_type"],
        severity=data["severity"],
        title=data["title"],
        detail=data.get("detail"),
        triggered_at=datetime.fromisoformat(data["triggered_at"]),
        status=data.get("status", "unread"),
    )


def serialize_alert(alert: Alert) -> str:
    """Serialkan ``Alert`` ke string JSON."""
    return json.dumps(alert_to_dict(alert))


def deserialize_alert(payload: str) -> Alert:
    """Deserialkan string JSON menjadi ``Alert``."""
    return alert_from_dict(json.loads(payload))
