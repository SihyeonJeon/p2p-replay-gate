from __future__ import annotations

import json
from importlib import resources
from typing import Any

from .adapters import normalize_activity_map


def load_activity_map(pack: str) -> dict[str, str]:
    data = _read_pack_json(pack, "activity_map.json")
    return normalize_activity_map(data, f"pack:{pack}/activity_map.json")


def load_manifest(pack: str) -> dict[str, Any]:
    return _read_pack_json(pack, "manifest.json")


def _read_pack_json(pack: str, filename: str) -> dict[str, Any]:
    if not pack.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid pack name: {pack}")
    try:
        text = resources.files("p2p_replay_gate.mapping_packs").joinpath(pack, filename).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"unknown mapping pack: {pack}") from exc
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"pack {pack}/{filename} must contain a JSON object")
    return data
