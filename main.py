from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

import db
import display
import ddragon
from api import RiotClient, RiotAPIError, make_session

load_dotenv()

console = Console()

# ── Queue filter shorthand ─────────────────────────────────────────────────────
QUEUE_ALIASES: dict[str, int] = {
    "ranked":       420,
    "ranked-solo":  420,
    "rankedsolo":   420,
    "solo":         420,
    "ranked-flex":  440,
    "rankedflex":   440,
    "flex":         440,
    "aram":         450,
    "normal":       400,
    "normal-blind": 430,
    "blind":        430,
    "draft":        400,
    "quickplay":    490,
    "arena":        1700,
    "urf":          900,
}


def _parse_riot_id(riot_id: str) -> tuple[str, str]:
    if "#" not in riot_id:
        console.print(
            f"[red]Invalid Riot ID '{riot_id}'. Expected format: GameName#TAG[/red]"
        )
        sys.exit(1)
    name, tag = riot_id.rsplit("#", 1)
    return name.strip(), tag.strip()


def _require_env() -> tuple[str, str]:
    api_key  = os.getenv("RIOT_API_KEY", "")
    platform = os.getenv("RIOT_PLATFORM", "na1")
    if not api_key or api_key.startswith("RGAPI-your"):
        console.print("[red]RIOT_API_KEY not set. Copy .env.example → .env and fill it in.[/red]")
        sys.exit(1)
    return api_key, platform


def _resolve_queue(queue_str: Optional[str]) -> Optional[int]:
    if queue_str is None:
        return None
    lower = queue_str.lower().strip()
    if lower in QUEUE_ALIASES:
        return QUEUE_ALIASES[lower]
    try:
        return int(lower)
    except ValueError:
        console.print(f"[red]Unknown queue '{queue_str}'. Use a queue ID or one of: "
                      + ", ".join(QUEUE_ALIASES.keys()) + "[/red]")
        sys.exit(1)


# ── Fetch pipeline ─────────────────────────────────────────────────────────────

async def _fetch_player(
    api_key: str,
    platform: str,
    game_name: str,
    tag_line: str,
    incremental: bool = True,
    queue_id: Optional[int] = None,
) -> None:
    async with make_session() as session:
        client = RiotClient(api_key=api_key, platform=platform, session=session)

        # 1. Resolve PUUID
        with console.status(f"[cyan]Looking up {game_name}#{tag_line}…[/cyan]"):
            try:
                account = await client.get_account_by_riot_id(game_name, tag_line)
            except RiotAPIError as e:
                console.print(f"[red]Account lookup failed: {e}[/red]")
                return

        puuid = account["puuid"]
        # Use the authoritative casing from the API
        game_name = account.get("gameName", game_name)
        tag_line  = account.get("tagLine",  tag_line)

        db.upsert_player(puuid, game_name, tag_line)
        console.print(f"[green]✓[/green]  [bold]{game_name}#{tag_line}[/bold]  [dim]PUUID: {puuid[:16]}…[/dim]")

        # 2. Determine stop point for incremental mode
        stop_at: Optional[str] = None
        if incremental:
            existing = db.get_player_match_ids(puuid)
            if existing:
                # Stop when we hit any already-stored match
                stop_at = existing[0]  # player_matches aren't ordered, but any will do
                # Actually we want the most recent — query DB
                with db.get_connection() as conn:
                    row = conn.execute("""
                        SELECT pm.match_id FROM player_matches pm
                        JOIN matches m ON m.match_id = pm.match_id
                        WHERE pm.puuid = ?
                        ORDER BY m.game_creation DESC LIMIT 1
                    """, (puuid,)).fetchone()
                stop_at = row["match_id"] if row else None

        # 3. Get all match IDs to fetch
        with console.status("[cyan]Fetching match ID list…[/cyan]"):
            match_ids = await client.get_all_match_ids(
                puuid, stop_at=stop_at, queue=queue_id
            )

        if not match_ids:
            console.print("[yellow]No new matches to fetch.[/yellow]")
            db.update_player_fetched(puuid)
            return

        console.print(f"  Found [bold]{len(match_ids)}[/bold] new match(es) to fetch")

        # 4. Fetch each match + timeline with progress bar
        fetched = skipped = errors = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Fetching matches…", total=len(match_ids))

            # Process in batches of 10 (async parallel, respects semaphore)
            batch_size = 10
            for i in range(0, len(match_ids), batch_size):
                batch = match_ids[i:i + batch_size]

                async def fetch_one(mid: str) -> None:
                    nonlocal fetched, skipped, errors
                    try:
                        if db.has_match(mid):
                            # Match data already stored; just link player
                            db.link_player_match(puuid, mid)
                            skipped += 1
                            return

                        match_data = await client.get_match(mid)
                        is_new = db.insert_match(match_data)

                        if is_new:
                            parts = match_data.get("info", {}).get("participants", [])
                            db.insert_participants(mid, parts)

                        db.link_player_match(puuid, mid)

                        # Timeline
                        try:
                            tl = await client.get_match_timeline(mid)
                            db.insert_timeline(mid, tl)
                        except RiotAPIError:
                            pass  # timeline is best-effort

                        fetched += 1
                    except RiotAPIError as e:
                        errors += 1
                        if e.status != 404:
                            progress.console.print(f"  [red]Error {mid}: {e}[/red]")

                await asyncio.gather(*[fetch_one(mid) for mid in batch])
                progress.advance(task, len(batch))

        db.update_player_fetched(puuid)

        console.print(
            f"\n[green]Done.[/green]  "
            f"Fetched [bold]{fetched}[/bold]  ·  "
            f"Skipped (already stored) [dim]{skipped}[/dim]  ·  "
            f"Errors [red]{errors}[/red]"
        )


# ── Command handlers ───────────────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> None:
    """Fetch new matches since last fetch (incremental)."""
    api_key, platform = _require_env()
    game_name, tag_line = _parse_riot_id(args.player)
    queue_id = _resolve_queue(args.queue)
    db.init_db()
    asyncio.run(_fetch_player(api_key, platform, game_name, tag_line,
                              incremental=True, queue_id=queue_id))


def cmd_full(args: argparse.Namespace) -> None:
    """Fetch ALL matches from the beginning of Riot's API history."""
    api_key, platform = _require_env()
    game_name, tag_line = _parse_riot_id(args.player)
    queue_id = _resolve_queue(args.queue)
    db.init_db()
    console.print("[yellow]Note: fetching full history. This may take a long time.[/yellow]")
    asyncio.run(_fetch_player(api_key, platform, game_name, tag_line,
                              incremental=False, queue_id=queue_id))


def cmd_view(args: argparse.Namespace) -> None:
    """View a player's match list."""
    db.init_db()
    game_name, tag_line = _parse_riot_id(args.player)

    player = db.get_player_by_riot_id(game_name, tag_line)
    if not player:
        console.print(f"[red]Player '{game_name}#{tag_line}' not found in database. "
                      f"Run `fetch` first.[/red]")
        return

    queue_id  = _resolve_queue(args.queue)
    limit     = max(1, args.limit)
    page      = max(1, args.page)
    offset    = (page - 1) * limit
    champion  = args.champion or None

    rows  = db.get_player_matches(player["puuid"], limit=limit, offset=offset,
                                  queue_id=queue_id, champion=champion)
    total = db.count_player_matches(player["puuid"])

    if not rows:
        console.print(f"[yellow]No matches found for {game_name}#{tag_line} "
                      f"(page {page}, limit {limit}).[/yellow]")
        return

    display.print_match_list(
        rows, f"{game_name}#{tag_line}", total, page, limit
    )


def cmd_match(args: argparse.Namespace) -> None:
    """Show the full scoreboard for a single match."""
    db.init_db()

    with console.status("[cyan]Loading Data Dragon cache…[/cyan]", spinner="dots"):
        ddragon.ensure_loaded()

    match_row = db.get_match_row(args.match_id)
    if not match_row:
        console.print(f"[red]Match '{args.match_id}' not found in database.[/red]")
        return

    participants = db.get_match_all_participants(args.match_id)
    display.print_match_detail(match_row, participants)


def cmd_stats(args: argparse.Namespace) -> None:
    """Show aggregate stats for a player."""
    db.init_db()
    game_name, tag_line = _parse_riot_id(args.player)

    player = db.get_player_by_riot_id(game_name, tag_line)
    if not player:
        console.print(f"[red]Player '{game_name}#{tag_line}' not found in database. "
                      f"Run `fetch` first.[/red]")
        return

    agg = db.get_aggregate_stats(player["puuid"])
    display.print_stats(player, agg)


def cmd_timeline(args: argparse.Namespace) -> None:
    """Show a summary of timeline events for a match."""
    db.init_db()

    with console.status("[cyan]Loading Data Dragon cache…[/cyan]", spinner="dots"):
        ddragon.ensure_loaded()

    timeline = db.get_timeline(args.match_id)
    if not timeline:
        console.print(f"[red]Timeline for '{args.match_id}' not found. "
                      f"It may not have been fetched yet.[/red]")
        return

    display.print_timeline_summary(args.match_id, timeline)


def cmd_players(args: argparse.Namespace) -> None:
    """List all tracked players."""
    db.init_db()
    players = db.list_players()
    if not players:
        console.print("[yellow]No players in database. Run `fetch` first.[/yellow]")
        return
    display.print_players(players)


def cmd_dbstats(args: argparse.Namespace) -> None:
    """Show database summary."""
    db.init_db()
    stats = db.get_db_stats()
    console.print(Panel(
        f"  [bold]Matches stored:[/bold]   {stats['matches']}\n"
        f"  [bold]Players tracked:[/bold]  {stats['players']}\n"
        f"  [bold]Timelines stored:[/bold] {stats['timelines']}",
        title="[bold cyan]Database Stats[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))


def cmd_reset(args: argparse.Namespace) -> None:
    """Wipe all data from the database."""
    console.print("[yellow]This will delete ALL match data, players, and timelines.[/yellow]")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        console.print("[dim]Cancelled.[/dim]")
        return
    db.reset_db()
    console.print("[green]Database cleared.[/green]")


# ── Argument parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python main.py",
        description="League of Legends match history fetcher & viewer",
    )
    sub = p.add_subparsers(dest="cmd")

    # fetch
    f = sub.add_parser("fetch", help="Fetch new matches for a player (incremental)")
    f.add_argument("player", metavar="GameName#TAG")
    f.add_argument("--queue", metavar="QUEUE",
                   help="Filter by queue (e.g. ranked, aram, 420). Default: all queues")
    f.set_defaults(func=cmd_fetch)

    # full
    fu = sub.add_parser("full", help="Fetch ALL matches (from beginning of API history)")
    fu.add_argument("player", metavar="GameName#TAG")
    fu.add_argument("--queue", metavar="QUEUE",
                    help="Filter by queue. Default: all queues")
    fu.set_defaults(func=cmd_full)

    # view
    v = sub.add_parser("view", help="View match history for a player")
    v.add_argument("player", metavar="GameName#TAG")
    v.add_argument("--page",      type=int, default=1, metavar="N")
    v.add_argument("--limit",     type=int, default=20, metavar="N")
    v.add_argument("--queue",     metavar="QUEUE",
                   help="Filter by queue (e.g. ranked, aram)")
    v.add_argument("--champion",  metavar="NAME", help="Filter by champion name")
    v.set_defaults(func=cmd_view)

    # match
    m = sub.add_parser("match", help="Show full scoreboard for a match")
    m.add_argument("match_id", metavar="MATCH_ID")
    m.set_defaults(func=cmd_match)

    # stats
    s = sub.add_parser("stats", help="Aggregate stats for a player")
    s.add_argument("player", metavar="GameName#TAG")
    s.set_defaults(func=cmd_stats)

    # timeline
    tl = sub.add_parser("timeline", help="Timeline event summary for a match")
    tl.add_argument("match_id", metavar="MATCH_ID")
    tl.set_defaults(func=cmd_timeline)

    # players
    sub.add_parser("players", help="List all tracked players") \
       .set_defaults(func=cmd_players)

    # dbstats
    sub.add_parser("dbstats", help="Show database summary") \
       .set_defaults(func=cmd_dbstats)

    # reset
    sub.add_parser("reset", help="Wipe all data from the database") \
       .set_defaults(func=cmd_reset)

    return p


def _print_help() -> None:
    db.init_db()
    stats = db.get_db_stats()
    console.print(Panel(
        "[bold cyan]League Match History[/bold cyan]\n\n"
        "  [bold]fetch[/bold]  [dim]GameName#TAG[/dim]         Fetch new matches (incremental)\n"
        "  [bold]full[/bold]   [dim]GameName#TAG[/dim]         Fetch complete history\n"
        "  [bold]view[/bold]   [dim]GameName#TAG[/dim]         Browse match list\n"
        "  [bold]match[/bold]  [dim]MATCH_ID[/dim]             Full scoreboard\n"
        "  [bold]stats[/bold]  [dim]GameName#TAG[/dim]         Aggregate stats\n"
        "  [bold]timeline[/bold] [dim]MATCH_ID[/dim]           Timeline event summary\n"
        "  [bold]players[/bold]                    List tracked players\n"
        "  [bold]dbstats[/bold]                    Database summary\n"
        "  [bold]reset[/bold]                      Wipe database\n\n"
        f"  [dim]Matches stored: {stats['matches']}  ·  "
        f"Players: {stats['players']}  ·  "
        f"Timelines: {stats['timelines']}[/dim]",
        title="[bold]Usage[/bold]",
        border_style="blue",
        expand=False,
    ))


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if not args.cmd:
        _print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
