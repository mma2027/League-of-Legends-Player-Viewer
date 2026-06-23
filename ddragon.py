from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).parent
_ITEMS_PATH    = _DIR / "ddragon_items.json"
_CHAMPS_PATH   = _DIR / "ddragon_champions.json"
_SPELLS_PATH   = _DIR / "ddragon_spells.json"
_VERSION_PATH  = _DIR / "ddragon_version.txt"

_items_map:   dict[int, str] = {}
_champs_map:  dict[int, str] = {}
_spells_map:  dict[int, str] = {}
_loaded = False


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


def _fetch_json(url: str) -> dict | list:
    return json.loads(_fetch_text(url))


def _get_latest_version() -> str:
    versions = _fetch_json("https://ddragon.leagueoflegends.com/api/versions.json")
    return versions[0]


def _build_caches(version: str) -> None:
    base = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US"

    # Items
    item_data = _fetch_json(f"{base}/item.json")
    items = {int(k): v["name"] for k, v in item_data["data"].items()}
    _ITEMS_PATH.write_text(json.dumps(items))

    # Champions (keyed by numeric key, not string name)
    champ_data = _fetch_json(f"{base}/champion.json")
    champs: dict[int, str] = {}
    for c in champ_data["data"].values():
        champs[int(c["key"])] = c["name"]
    _CHAMPS_PATH.write_text(json.dumps({str(k): v for k, v in champs.items()}))

    # Summoner spells
    spell_data = _fetch_json(f"{base}/summoner.json")
    spells: dict[int, str] = {}
    for s in spell_data["data"].values():
        spells[int(s["key"])] = s["name"]
    _SPELLS_PATH.write_text(json.dumps({str(k): v for k, v in spells.items()}))

    _VERSION_PATH.write_text(version)


def _load() -> None:
    global _loaded, _items_map, _champs_map, _spells_map

    if _loaded:
        return

    needs_fetch = not all(p.exists() for p in [_ITEMS_PATH, _CHAMPS_PATH, _SPELLS_PATH])

    if not needs_fetch:
        # Check if cached version matches latest
        try:
            cached_ver = _VERSION_PATH.read_text().strip() if _VERSION_PATH.exists() else ""
            latest_ver = _get_latest_version()
            if cached_ver != latest_ver:
                needs_fetch = True
        except Exception:
            pass  # network error — use cached data

    if needs_fetch:
        try:
            version = _get_latest_version()
            _build_caches(version)
        except Exception:
            pass  # if fetch fails and no cache, maps remain empty

    if _ITEMS_PATH.exists():
        _items_map   = {int(k): v for k, v in json.loads(_ITEMS_PATH.read_text()).items()}
    if _CHAMPS_PATH.exists():
        _champs_map  = {int(k): v for k, v in json.loads(_CHAMPS_PATH.read_text()).items()}
    if _SPELLS_PATH.exists():
        _spells_map  = {int(k): v for k, v in json.loads(_SPELLS_PATH.read_text()).items()}

    _loaded = True


def get_item_name(item_id: Optional[int]) -> str:
    if item_id is None or item_id == 0:
        return "—"
    _load()
    return _items_map.get(item_id, f"#{item_id}")


def get_champion_name(champion_id: Optional[int]) -> str:
    if champion_id is None:
        return "Unknown"
    _load()
    return _champs_map.get(champion_id, f"#{champion_id}")


def get_spell_name(spell_id: Optional[int]) -> str:
    if spell_id is None:
        return "—"
    _load()
    return _spells_map.get(spell_id, f"#{spell_id}")


def get_item_names(ids: list[Optional[int]]) -> list[str]:
    return [get_item_name(i) for i in ids if i is not None and i != 0]


def ensure_loaded() -> str:
    """Load caches and return the version string (for display)."""
    _load()
    try:
        return _VERSION_PATH.read_text().strip()
    except Exception:
        return "unknown"
