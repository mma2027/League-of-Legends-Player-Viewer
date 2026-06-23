from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import ddragon

console = Console()

# ── Queue ID → human name ─────────────────────────────────────────────────────
QUEUE_NAMES: dict[int, str] = {
    0:   "Custom",
    2:   "Normal (Blind)",
    4:   "Ranked Solo",
    6:   "Ranked Solo (old)",
    7:   "Co-op vs AI",
    8:   "Normal (3v3)",
    9:   "Ranked Flex 3v3",
    14:  "Normal (Draft)",
    16:  "Dominion (Blind)",
    17:  "Dominion (Draft)",
    25:  "Dominion Co-op",
    31:  "Co-op vs AI (Intro)",
    32:  "Co-op vs AI (Beginner)",
    33:  "Co-op vs AI (Intermediate)",
    41:  "Ranked Team 3v3",
    42:  "Ranked Team 5v5",
    52:  "Co-op vs AI (3v3)",
    61:  "Team Builder",
    65:  "ARAM",
    67:  "ARAM Co-op vs AI",
    70:  "One for All",
    72:  "Snowdown 1v1",
    73:  "Snowdown 2v2",
    75:  "Hexakill (SR)",
    76:  "URF",
    78:  "One for All (Mirror)",
    83:  "Co-op vs AI (URF)",
    98:  "Hexakill (TT)",
    100: "ARAM (Butcher's Bridge)",
    310: "Nemesis",
    313: "Black Market Brawlers",
    317: "Sion Brawl",
    325: "All Random",
    400: "Normal (Draft)",
    410: "Ranked Flex (old)",
    420: "Ranked Solo",
    430: "Normal (Blind)",
    440: "Ranked Flex",
    450: "ARAM",
    460: "Normal (3v3 Blind)",
    470: "Ranked Flex (3v3)",
    480: "Swift Play",
    490: "Normal (Quickplay)",
    600: "Blood Hunt",
    610: "Dark Star: Singularity",
    700: "Clash",
    720: "Clash (ARAM)",
    800: "Co-op vs AI (3v3 Int)",
    810: "Co-op vs AI (3v3 Intro)",
    820: "Co-op vs AI (3v3 Beg)",
    830: "Co-op vs AI (Intro)",
    840: "Co-op vs AI (Beginner)",
    850: "Co-op vs AI (Intermediate)",
    900: "URF",
    910: "Ascension",
    920: "Legend of the Poro King",
    940: "Nexus Siege",
    950: "Doom Bots (Voting)",
    960: "Doom Bots",
    980: "Star Guardian Invasion (Normal)",
    990: "Star Guardian Invasion (Onslaught)",
    1000:"PROJECT: Hunters",
    1010:"Snow ARURF",
    1020:"One for All",
    1030:"Odyssey Extraction (Intro)",
    1040:"Odyssey Extraction (Cadet)",
    1050:"Odyssey Extraction (Crewmember)",
    1060:"Odyssey Extraction (Captain)",
    1070:"Odyssey Extraction (Onslaught)",
    1090:"Teamfight Tactics",
    1100:"Ranked TFT",
    1110:"TFT Tutorial",
    1111:"TFT Simulation",
    1200:"Nexus Blitz",
    1300:"Nexus Blitz",
    1400:"Ultimate Spellbook",
    1700:"Arena",
    1710:"Arena (duo)",
    2000:"Tutorial 1",
    2010:"Tutorial 2",
    2020:"Tutorial 3",
}


def queue_name(qid: Optional[int]) -> str:
    if qid is None:
        return "—"
    return QUEUE_NAMES.get(qid, f"Queue {qid}")


def fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s"


def fmt_kda(k: Optional[int], d: Optional[int], a: Optional[int]) -> str:
    if k is None:
        return "—"
    d_str = str(d) if d is not None else "?"
    return f"{k}/{d_str}/{a or 0}"


def fmt_kda_ratio(k: Optional[int], d: Optional[int], a: Optional[int]) -> str:
    if k is None or d is None:
        return "—"
    deaths = max(d, 1)
    ratio = (k + (a or 0)) / deaths
    return f"{ratio:.2f}"


def fmt_gold(gold: Optional[int]) -> str:
    if gold is None:
        return "—"
    return f"{gold / 1000:.1f}k"


def fmt_dmg(dmg: Optional[int]) -> str:
    if dmg is None:
        return "—"
    if dmg >= 1000:
        return f"{dmg / 1000:.1f}k"
    return str(dmg)


def fmt_cs(total: Optional[int], neutral: Optional[int]) -> str:
    if total is None:
        return "—"
    return str((total or 0) + (neutral or 0))


def fmt_date(epoch_ms: Optional[int]) -> str:
    if epoch_ms is None:
        return "—"
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%b %d, %Y %H:%M")


def fmt_date_short(epoch_ms: Optional[int]) -> str:
    if epoch_ms is None:
        return "—"
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone()
    # Show "X days ago" for recent, otherwise date
    diff_days = (datetime.now(tz=timezone.utc) -
                 datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)).days
    if diff_days == 0:
        return "Today"
    if diff_days == 1:
        return "Yesterday"
    if diff_days < 7:
        return f"{diff_days}d ago"
    if diff_days < 365:
        return dt.strftime("%b %d")
    return dt.strftime("%b %d, %Y")


def win_text(win: Optional[int], early_surr: Optional[int] = None) -> Text:
    if win is None:
        return Text("—", style="dim")
    if win:
        return Text("WIN", style="bold green")
    if early_surr:
        return Text("REMAKE", style="dim yellow")
    return Text("LOSS", style="bold red")


def role_display(team_pos: Optional[str], indiv_pos: Optional[str]) -> str:
    pos = team_pos or indiv_pos or "—"
    return pos.replace("_", " ").title() if pos != "—" else "—"


# ── Match list table ──────────────────────────────────────────────────────────

def print_match_list(rows: list[sqlite3.Row], player_name: str,
                     total: int, page: int, limit: int) -> None:
    t = Table(
        title=f"[bold cyan]{player_name}[/bold cyan]  [dim]·  {total} matches stored"
              f"  ·  page {page}[/dim]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
        show_lines=False,
        expand=True,
    )
    t.add_column("Date",      style="dim",  no_wrap=True, width=11)
    t.add_column("Champion",  style="bold", no_wrap=True, min_width=14)
    t.add_column("Role",      no_wrap=True, width=10)
    t.add_column("Queue",     no_wrap=True, min_width=16)
    t.add_column("KDA",       justify="center", no_wrap=True, width=10)
    t.add_column("CS",        justify="right", width=5)
    t.add_column("DMG",       justify="right", width=7)
    t.add_column("Gold",      justify="right", width=6)
    t.add_column("Vision",    justify="right", width=7)
    t.add_column("Duration",  justify="right", width=9)
    t.add_column("Result",    justify="center", no_wrap=True, width=7)

    for r in rows:
        cs = fmt_cs(r["total_minions_killed"], r["neutral_minions_killed"])
        t.add_row(
            fmt_date_short(r["game_creation"]),
            r["champion_name"] or "—",
            role_display(r["team_position"], r["individual_position"]),
            queue_name(r["queue_id"]),
            fmt_kda(r["kills"], r["deaths"], r["assists"]),
            cs,
            fmt_dmg(r["total_damage_dealt_to_champions"]),
            fmt_gold(r["gold_earned"]),
            str(r["vision_score"] or 0),
            fmt_duration(r["game_duration"]),
            win_text(r["win"], r["game_ended_in_early_surrender"]),
        )

    console.print(t)
    pages = max(1, (total + limit - 1) // limit)
    console.print(
        f"  [dim]Page {page}/{pages}  ·  "
        f"use --page N to navigate  ·  "
        f"--limit N to change per-page count[/dim]\n"
    )


# ── Full match scoreboard ─────────────────────────────────────────────────────

def print_match_detail(match_row: sqlite3.Row,
                       participants: list[sqlite3.Row]) -> None:
    qname  = queue_name(match_row["queue_id"])
    dur    = fmt_duration(match_row["game_duration"])
    date   = fmt_date(match_row["game_creation"])
    ver    = (match_row["game_version"] or "").split(".")
    patch  = ".".join(ver[:2]) if len(ver) >= 2 else match_row["game_version"] or "?"

    console.print(Panel(
        f"[bold]{match_row['match_id']}[/bold]  [dim]·[/dim]  "
        f"[cyan]{qname}[/cyan]  [dim]·[/dim]  "
        f"{dur}  [dim]·[/dim]  {date}  [dim]·[/dim]  patch [yellow]{patch}[/yellow]",
        border_style="dim",
        expand=True,
    ))

    blue  = [p for p in participants if p["team_id"] == 100]
    red   = [p for p in participants if p["team_id"] == 200]
    blue_win = blue[0]["win"] if blue else 0

    for team, label, color in [
        (blue, "BLUE TEAM", "blue"),
        (red,  "RED TEAM",  "red"),
    ]:
        result  = "WIN" if (team[0]["win"] if team else 0) else "LOSS"
        res_col = "green" if result == "WIN" else "red"

        t = Table(
            title=f"[bold {color}]{label}[/bold {color}]  [{res_col}]{result}[/{res_col}]",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold dim",
            expand=True,
        )
        t.add_column("Champion",  style="bold", min_width=14)
        t.add_column("Player",    min_width=16, no_wrap=True)
        t.add_column("KDA",       justify="center", width=10)
        t.add_column("KDA Ratio", justify="right",  width=9)
        t.add_column("CS",        justify="right",  width=5)
        t.add_column("DMG",       justify="right",  width=7)
        t.add_column("Gold",      justify="right",  width=6)
        t.add_column("Vision",    justify="right",  width=7)
        t.add_column("Items",     min_width=30)

        for p in team:
            name_str = f"{p['riot_id_game_name'] or ''}#{p['riot_id_tag_line'] or ''}"
            item_ids = [p[f"item{i}"] for i in range(6)]
            trinket  = p["item6"]
            item_names = ddragon.get_item_names(item_ids)
            trinket_name = ddragon.get_item_name(trinket)
            items_str = "  ".join(item_names) or "—"
            if trinket_name != "—":
                items_str += f"  [{trinket_name}]"

            t.add_row(
                p["champion_name"] or "—",
                name_str,
                fmt_kda(p["kills"], p["deaths"], p["assists"]),
                fmt_kda_ratio(p["kills"], p["deaths"], p["assists"]),
                fmt_cs(p["total_minions_killed"], p["neutral_minions_killed"]),
                fmt_dmg(p["total_damage_dealt_to_champions"]),
                fmt_gold(p["gold_earned"]),
                str(p["vision_score"] or 0),
                items_str,
            )
        console.print(t)


# ── Stats panel ───────────────────────────────────────────────────────────────

def print_stats(player_row: sqlite3.Row, agg: dict) -> None:
    o = agg["overall"]
    games = o["games"] or 0
    wins  = o["wins"] or 0
    wr    = wins / games * 100 if games else 0
    k     = o["avg_kills"] or 0
    d     = o["avg_deaths"] or 1
    a     = o["avg_assists"] or 0
    kda   = (k + a) / max(d, 1)

    header = (
        f"[bold cyan]{player_row['game_name']}#{player_row['tag_line']}[/bold cyan]"
        f"  [dim]·  All Time[/dim]\n\n"
        f"  [bold]Games:[/bold] {games}    "
        f"[bold]Win Rate:[/bold] [{'green' if wr >= 50 else 'red'}]{wr:.1f}%[/{'green' if wr >= 50 else 'red'}]    "
        f"[bold]Avg KDA:[/bold] {k:.1f}/{d:.1f}/{a:.1f}  ({kda:.2f})"
    )
    if o["pentas"] and o["pentas"] > 0:
        header += f"    [bold yellow]Pentas:[/bold yellow] {int(o['pentas'])}"
    if o["quadras"] and o["quadras"] > 0:
        header += f"    [bold]Quadras:[/bold] {int(o['quadras'])}"

    console.print(Panel(header, border_style="cyan", expand=False))

    # By queue
    if agg["by_queue"]:
        qt = Table(title="By Queue", box=box.SIMPLE_HEAVY, show_header=True,
                   header_style="bold dim", expand=False)
        qt.add_column("Queue",    min_width=20)
        qt.add_column("Games",    justify="right", width=7)
        qt.add_column("Win Rate", justify="right", width=9)
        qt.add_column("Avg KDA",  justify="right", width=16)

        for q in agg["by_queue"]:
            qg = q["games"] or 0
            qw = q["wins"] or 0
            qwr = qw / qg * 100 if qg else 0
            qk = q["avg_kills"] or 0
            qd = q["avg_deaths"] or 1
            qa = q["avg_assists"] or 0
            qkda = (qk + qa) / max(qd, 1)
            col = "green" if qwr >= 50 else "red"
            qt.add_row(
                queue_name(q["queue_id"]),
                str(qg),
                f"[{col}]{qwr:.1f}%[/{col}]",
                f"{qk:.1f}/{qd:.1f}/{qa:.1f}  ({qkda:.2f})",
            )
        console.print(qt)

    # By champion
    if agg["by_champ"]:
        ct = Table(title="Top Champions", box=box.SIMPLE_HEAVY, show_header=True,
                   header_style="bold dim", expand=False)
        ct.add_column("Champion", style="bold", min_width=14)
        ct.add_column("Games",    justify="right", width=7)
        ct.add_column("Win Rate", justify="right", width=9)
        ct.add_column("Avg KDA",  justify="right", width=16)

        for c in agg["by_champ"]:
            cg = c["games"] or 0
            cw = c["wins"] or 0
            cwr = cw / cg * 100 if cg else 0
            ck = c["avg_kills"] or 0
            cd = c["avg_deaths"] or 1
            ca = c["avg_assists"] or 0
            ckda = (ck + ca) / max(cd, 1)
            col = "green" if cwr >= 50 else "red"
            ct.add_row(
                c["champion_name"] or "Unknown",
                str(cg),
                f"[{col}]{cwr:.1f}%[/{col}]",
                f"{ck:.1f}/{cd:.1f}/{ca:.1f}  ({ckda:.2f})",
            )
        console.print(ct)


# ── Timeline summary ──────────────────────────────────────────────────────────

def print_timeline_summary(match_id: str, timeline: dict) -> None:
    info   = timeline.get("info", {})
    frames = info.get("frames", [])

    # Collect all events
    all_events: list[dict] = []
    for frame in frames:
        all_events.extend(frame.get("events", []))

    # Group by type
    type_counts: dict[str, int] = {}
    for ev in all_events:
        etype = ev.get("type", "UNKNOWN")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    kills          = [e for e in all_events if e.get("type") == "CHAMPION_KILL"]
    item_purchases = [e for e in all_events if e.get("type") == "ITEM_PURCHASED"]
    buildings      = [e for e in all_events if e.get("type") == "BUILDING_KILL"]
    elite_monsters = [e for e in all_events if e.get("type") == "ELITE_MONSTER_KILL"]

    console.print(Panel(
        f"[bold]{match_id}[/bold]  [dim]·  Timeline  ·  {len(frames)} frames  ·  {len(all_events)} events[/dim]",
        border_style="dim",
    ))

    # Event type summary
    et = Table(title="Event Types", box=box.SIMPLE, header_style="bold dim", expand=False)
    et.add_column("Event Type", min_width=30)
    et.add_column("Count", justify="right", width=8)
    for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        et.add_row(etype, str(cnt))
    console.print(et)

    # Kill feed (first 20)
    if kills:
        kt = Table(title=f"Kills ({len(kills)} total, showing first 20)",
                   box=box.SIMPLE, header_style="bold dim", expand=False)
        kt.add_column("Time", width=8)
        kt.add_column("Killer ID", justify="center", width=10)
        kt.add_column("Victim ID", justify="center", width=10)
        kt.add_column("Assistants", width=20)
        for ev in kills[:20]:
            ts_s = ev.get("timestamp", 0) // 1000
            m, s = divmod(ts_s, 60)
            assists = ", ".join(str(a) for a in ev.get("assistingParticipantIds", []))
            kt.add_row(
                f"{m}:{s:02d}",
                str(ev.get("killerId", "?")),
                str(ev.get("victimId", "?")),
                assists or "—",
            )
        console.print(kt)

    # First few item purchases
    if item_purchases:
        it = Table(title=f"Item Purchases ({len(item_purchases)} total, showing first 20)",
                   box=box.SIMPLE, header_style="bold dim", expand=False)
        it.add_column("Time", width=8)
        it.add_column("Participant", justify="center", width=12)
        it.add_column("Item", min_width=30)
        for ev in item_purchases[:20]:
            ts_s = ev.get("timestamp", 0) // 1000
            m, s = divmod(ts_s, 60)
            item_name = ddragon.get_item_name(ev.get("itemId"))
            it.add_row(
                f"{m}:{s:02d}",
                str(ev.get("participantId", "?")),
                item_name,
            )
        console.print(it)


# ── Players list ──────────────────────────────────────────────────────────────

def print_players(player_rows: list[sqlite3.Row]) -> None:
    t = Table(title="Tracked Players", box=box.SIMPLE_HEAVY,
              show_header=True, header_style="bold", expand=False)
    t.add_column("#",        justify="right", width=4)
    t.add_column("Player",   style="bold", min_width=20)
    t.add_column("PUUID",    style="dim", min_width=20)
    t.add_column("Level",    justify="right", width=7)
    t.add_column("Last Fetched", width=18)

    for i, p in enumerate(player_rows, 1):
        name = f"{p['game_name']}#{p['tag_line']}"
        t.add_row(
            str(i),
            name,
            (p["puuid"] or "")[:24] + "…",
            str(p["summoner_level"] or "—"),
            fmt_date(p["last_fetched_at"]) if p["last_fetched_at"] else "[dim]never[/dim]",
        )
    console.print(t)
