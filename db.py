from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

DB_PATH = Path(__file__).parent / "matches.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid            TEXT PRIMARY KEY,
    game_name        TEXT NOT NULL,
    tag_line         TEXT NOT NULL,
    summoner_level   INTEGER,
    profile_icon_id  INTEGER,
    last_fetched_at  INTEGER,
    created_at       INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER) * 1000)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id       TEXT PRIMARY KEY,
    game_creation  INTEGER,
    game_duration  INTEGER,
    game_mode      TEXT,
    game_type      TEXT,
    game_version   TEXT,
    queue_id       INTEGER,
    platform_id    TEXT,
    raw_json       TEXT NOT NULL,
    fetched_at     INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER) * 1000)
);

CREATE TABLE IF NOT EXISTS participants (
    match_id                           TEXT NOT NULL,
    puuid                              TEXT NOT NULL,
    participant_id                     INTEGER,
    riot_id_game_name                  TEXT,
    riot_id_tag_line                   TEXT,
    summoner_id                        TEXT,
    summoner_level                     INTEGER,
    profile_icon                       INTEGER,
    champion_id                        INTEGER,
    champion_name                      TEXT,
    champion_transform                 INTEGER,
    team_id                            INTEGER,
    team_position                      TEXT,
    individual_position                TEXT,
    role                               TEXT,
    lane                               TEXT,
    win                                INTEGER,
    game_ended_in_early_surrender      INTEGER,
    game_ended_in_surrender            INTEGER,
    kills                              INTEGER,
    deaths                             INTEGER,
    assists                            INTEGER,
    gold_earned                        INTEGER,
    gold_spent                         INTEGER,
    total_minions_killed               INTEGER,
    neutral_minions_killed             INTEGER,
    total_damage_dealt_to_champions    INTEGER,
    physical_damage_dealt_to_champions INTEGER,
    magic_damage_dealt_to_champions    INTEGER,
    true_damage_dealt_to_champions     INTEGER,
    total_damage_taken                 INTEGER,
    damage_self_mitigated              INTEGER,
    vision_score                       INTEGER,
    wards_placed                       INTEGER,
    wards_killed                       INTEGER,
    detector_wards_placed              INTEGER,
    control_wards_bought               INTEGER,
    turret_kills                       INTEGER,
    inhibitor_kills                    INTEGER,
    baron_kills                        INTEGER,
    dragon_kills                       INTEGER,
    double_kills                       INTEGER,
    triple_kills                       INTEGER,
    quadra_kills                       INTEGER,
    penta_kills                        INTEGER,
    largest_killing_spree              INTEGER,
    item0 INTEGER, item1 INTEGER, item2 INTEGER,
    item3 INTEGER, item4 INTEGER, item5 INTEGER, item6 INTEGER,
    items_purchased                    INTEGER,
    summoner1_id INTEGER, summoner1_casts INTEGER,
    summoner2_id INTEGER, summoner2_casts INTEGER,
    spell1_casts INTEGER, spell2_casts INTEGER,
    spell3_casts INTEGER, spell4_casts INTEGER,
    time_played                        INTEGER,
    total_time_spent_dead              INTEGER,
    longest_time_spent_living          INTEGER,
    time_cc_others                     INTEGER,
    first_blood_kill                   INTEGER,
    first_blood_assist                 INTEGER,
    first_tower_kill                   INTEGER,
    first_tower_assist                 INTEGER,
    all_in_pings INTEGER, assist_me_pings INTEGER,
    command_pings INTEGER, danger_pings INTEGER,
    enemy_missing_pings INTEGER, on_my_way_pings INTEGER,
    push_pings INTEGER, retreat_pings INTEGER,
    perks_json                         TEXT,
    challenges_json                    TEXT,
    missions_json                      TEXT,
    PRIMARY KEY (match_id, puuid)
);

CREATE TABLE IF NOT EXISTS timelines (
    match_id   TEXT PRIMARY KEY,
    raw_json   TEXT NOT NULL,
    fetched_at INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER) * 1000)
);

CREATE TABLE IF NOT EXISTS player_matches (
    puuid    TEXT NOT NULL,
    match_id TEXT NOT NULL,
    PRIMARY KEY (puuid, match_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants(puuid);
CREATE INDEX IF NOT EXISTS idx_participants_match  ON participants(match_id);
CREATE INDEX IF NOT EXISTS idx_matches_queue       ON matches(queue_id);
CREATE INDEX IF NOT EXISTS idx_matches_creation    ON matches(game_creation DESC);
CREATE INDEX IF NOT EXISTS idx_pm_puuid            ON player_matches(puuid);
"""


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous  = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA cache_size   = -32000;
        PRAGMA temp_store   = MEMORY;
    """)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


# ── Players ──────────────────────────────────────────────────────────────────

def upsert_player(puuid: str, game_name: str, tag_line: str,
                  summoner_level: Optional[int] = None,
                  profile_icon_id: Optional[int] = None) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO players (puuid, game_name, tag_line, summoner_level, profile_icon_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(puuid) DO UPDATE SET
                game_name       = excluded.game_name,
                tag_line        = excluded.tag_line,
                summoner_level  = COALESCE(excluded.summoner_level, summoner_level),
                profile_icon_id = COALESCE(excluded.profile_icon_id, profile_icon_id)
        """, (puuid, game_name, tag_line, summoner_level, profile_icon_id))


def update_player_fetched(puuid: str) -> None:
    now_ms = int(time.time() * 1000)
    with get_connection() as conn:
        conn.execute(
            "UPDATE players SET last_fetched_at = ? WHERE puuid = ?",
            (now_ms, puuid)
        )


def get_player(puuid: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM players WHERE puuid = ?", (puuid,)
        ).fetchone()


def get_player_by_riot_id(game_name: str, tag_line: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM players WHERE LOWER(game_name) = LOWER(?) AND LOWER(tag_line) = LOWER(?)",
            (game_name, tag_line)
        ).fetchone()


def list_players() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM players ORDER BY last_fetched_at DESC NULLS LAST"
        ).fetchall()


# ── Matches ───────────────────────────────────────────────────────────────────

def insert_match(match_data: dict) -> bool:
    """Insert a match. Returns True if newly inserted, False if duplicate."""
    info = match_data.get("info", {})
    meta = match_data.get("metadata", {})
    match_id = meta.get("matchId", "")

    with get_connection() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO matches
                (match_id, game_creation, game_duration, game_mode, game_type,
                 game_version, queue_id, platform_id, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_id,
            info.get("gameCreation"),
            info.get("gameDuration"),
            info.get("gameMode"),
            info.get("gameType"),
            info.get("gameVersion"),
            info.get("queueId"),
            info.get("platformId"),
            json.dumps(match_data),
        ))
        return cur.rowcount > 0


def insert_participants(match_id: str, participants: list[dict]) -> None:
    def _g(p: dict, key: str, default=None):
        return p.get(key, default)

    rows = []
    for p in participants:
        rows.append((
            match_id,
            _g(p, "puuid", ""),
            _g(p, "participantId"),
            _g(p, "riotIdGameName"),
            _g(p, "riotIdTagline"),
            _g(p, "summonerId"),
            _g(p, "summonerLevel"),
            _g(p, "profileIcon"),
            _g(p, "championId"),
            _g(p, "championName"),
            _g(p, "championTransform"),
            _g(p, "teamId"),
            _g(p, "teamPosition"),
            _g(p, "individualPosition"),
            _g(p, "role"),
            _g(p, "lane"),
            int(_g(p, "win", False)),
            int(_g(p, "gameEndedInEarlySurrender", False)),
            int(_g(p, "gameEndedInSurrender", False)),
            _g(p, "kills"),
            _g(p, "deaths"),
            _g(p, "assists"),
            _g(p, "goldEarned"),
            _g(p, "goldSpent"),
            _g(p, "totalMinionsKilled"),
            _g(p, "neutralMinionsKilled"),
            _g(p, "totalDamageDealtToChampions"),
            _g(p, "physicalDamageDealtToChampions"),
            _g(p, "magicDamageDealtToChampions"),
            _g(p, "trueDamageDealtToChampions"),
            _g(p, "totalDamageTaken"),
            _g(p, "damageSelfMitigated"),
            _g(p, "visionScore"),
            _g(p, "wardsPlaced"),
            _g(p, "wardsKilled"),
            _g(p, "detectorWardsPlaced"),
            _g(p, "controlWardsPlaced"),  # note: some versions use controlWardsBought
            _g(p, "turretKills"),
            _g(p, "inhibitorKills"),
            _g(p, "baronKills"),
            _g(p, "dragonKills"),
            _g(p, "doubleKills"),
            _g(p, "tripleKills"),
            _g(p, "quadraKills"),
            _g(p, "pentaKills"),
            _g(p, "largestKillingSpree"),
            _g(p, "item0"), _g(p, "item1"), _g(p, "item2"),
            _g(p, "item3"), _g(p, "item4"), _g(p, "item5"), _g(p, "item6"),
            _g(p, "itemsPurchased"),
            _g(p, "summoner1Id"), _g(p, "summoner1Casts"),
            _g(p, "summoner2Id"), _g(p, "summoner2Casts"),
            _g(p, "spell1Casts"), _g(p, "spell2Casts"),
            _g(p, "spell3Casts"), _g(p, "spell4Casts"),
            _g(p, "timePlayed"),
            _g(p, "totalTimeSpentDead"),
            _g(p, "longestTimeSpentLiving"),
            _g(p, "timeCCingOthers"),
            int(_g(p, "firstBloodKill", False)),
            int(_g(p, "firstBloodAssist", False)),
            int(_g(p, "firstTowerKill", False)),
            int(_g(p, "firstTowerAssist", False)),
            _g(p, "allInPings"), _g(p, "assistMePings"),
            _g(p, "commandPings"), _g(p, "dangerPings"),
            _g(p, "enemyMissingPings"), _g(p, "onMyWayPings"),
            _g(p, "pushPings"), _g(p, "retreatPings"),
            json.dumps(_g(p, "perks")) if _g(p, "perks") else None,
            json.dumps(_g(p, "challenges")) if _g(p, "challenges") else None,
            json.dumps(_g(p, "missions")) if _g(p, "missions") else None,
        ))

    with get_connection() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO participants VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
        """, rows)


def insert_timeline(match_id: str, timeline_data: dict) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO timelines (match_id, raw_json)
            VALUES (?, ?)
        """, (match_id, json.dumps(timeline_data)))


def link_player_match(puuid: str, match_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO player_matches (puuid, match_id) VALUES (?, ?)",
            (puuid, match_id)
        )


def bulk_link_player_matches(puuid: str, match_ids: list[str]) -> None:
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO player_matches (puuid, match_id) VALUES (?, ?)",
            [(puuid, mid) for mid in match_ids]
        )


def has_match(match_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
    return row is not None


def has_player_match(puuid: str, match_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM player_matches WHERE puuid = ? AND match_id = ?",
            (puuid, match_id)
        ).fetchone()
    return row is not None


# ── Queries ───────────────────────────────────────────────────────────────────

def get_player_match_ids(puuid: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT match_id FROM player_matches WHERE puuid = ?", (puuid,)
        ).fetchall()
    return [r["match_id"] for r in rows]


def get_player_matches(puuid: str, limit: int = 20, offset: int = 0,
                       queue_id: Optional[int] = None,
                       champion: Optional[str] = None) -> list[sqlite3.Row]:
    filters = ["p.puuid = ?"]
    params: list = [puuid]

    if queue_id is not None:
        filters.append("m.queue_id = ?")
        params.append(queue_id)
    if champion:
        filters.append("LOWER(p.champion_name) = LOWER(?)")
        params.append(champion)

    where = " AND ".join(filters)
    params += [limit, offset]

    with get_connection() as conn:
        return conn.execute(f"""
            SELECT
                m.match_id, m.game_creation, m.game_duration,
                m.game_mode, m.game_type, m.game_version, m.queue_id,
                p.champion_name, p.team_position, p.individual_position,
                p.kills, p.deaths, p.assists,
                p.gold_earned, p.total_minions_killed, p.neutral_minions_killed,
                p.total_damage_dealt_to_champions,
                p.vision_score, p.win,
                p.item0, p.item1, p.item2, p.item3, p.item4, p.item5, p.item6,
                p.summoner1_id, p.summoner2_id,
                p.double_kills, p.triple_kills, p.quadra_kills, p.penta_kills,
                p.perks_json, p.game_ended_in_early_surrender, p.game_ended_in_surrender
            FROM player_matches pm
            JOIN matches m ON m.match_id = pm.match_id
            JOIN participants p ON p.match_id = pm.match_id AND p.puuid = pm.puuid
            WHERE {where}
            ORDER BY m.game_creation DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()


def count_player_matches(puuid: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM player_matches WHERE puuid = ?", (puuid,)
        ).fetchone()
    return row["n"] if row else 0


def get_match_all_participants(match_id: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM participants WHERE match_id = ?
            ORDER BY team_id, participant_id
        """, (match_id,)).fetchall()


def get_match_row(match_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()


def get_timeline(match_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT raw_json FROM timelines WHERE match_id = ?", (match_id,)
        ).fetchone()
    return json.loads(row["raw_json"]) if row else None


def get_aggregate_stats(puuid: str) -> dict:
    with get_connection() as conn:
        # Overall
        overall = conn.execute("""
            SELECT
                COUNT(*) as games,
                SUM(p.win) as wins,
                AVG(p.kills) as avg_kills,
                AVG(p.deaths) as avg_deaths,
                AVG(p.assists) as avg_assists,
                SUM(p.penta_kills) as pentas,
                SUM(p.quadra_kills) as quadras
            FROM player_matches pm
            JOIN participants p ON p.match_id = pm.match_id AND p.puuid = pm.puuid
            WHERE pm.puuid = ?
        """, (puuid,)).fetchone()

        # By queue
        by_queue = conn.execute("""
            SELECT
                m.queue_id,
                m.game_mode,
                COUNT(*) as games,
                SUM(p.win) as wins,
                AVG(p.kills) as avg_kills,
                AVG(p.deaths) as avg_deaths,
                AVG(p.assists) as avg_assists
            FROM player_matches pm
            JOIN matches m ON m.match_id = pm.match_id
            JOIN participants p ON p.match_id = pm.match_id AND p.puuid = pm.puuid
            WHERE pm.puuid = ?
            GROUP BY m.queue_id
            ORDER BY games DESC
            LIMIT 8
        """, (puuid,)).fetchall()

        # By champion
        by_champ = conn.execute("""
            SELECT
                p.champion_name,
                COUNT(*) as games,
                SUM(p.win) as wins,
                AVG(p.kills) as avg_kills,
                AVG(p.deaths) as avg_deaths,
                AVG(p.assists) as avg_assists
            FROM player_matches pm
            JOIN participants p ON p.match_id = pm.match_id AND p.puuid = pm.puuid
            WHERE pm.puuid = ?
            GROUP BY p.champion_name
            ORDER BY games DESC
            LIMIT 10
        """, (puuid,)).fetchall()

    return {
        "overall": overall,
        "by_queue": list(by_queue),
        "by_champ": list(by_champ),
    }


def get_db_stats() -> dict:
    with get_connection() as conn:
        matches   = conn.execute("SELECT COUNT(*) as n FROM matches").fetchone()["n"]
        players   = conn.execute("SELECT COUNT(*) as n FROM players").fetchone()["n"]
        timelines = conn.execute("SELECT COUNT(*) as n FROM timelines").fetchone()["n"]
    return {"matches": matches, "players": players, "timelines": timelines}


def reset_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS player_matches;
            DROP TABLE IF EXISTS participants;
            DROP TABLE IF EXISTS timelines;
            DROP TABLE IF EXISTS matches;
            DROP TABLE IF EXISTS players;
        """)
    init_db()
