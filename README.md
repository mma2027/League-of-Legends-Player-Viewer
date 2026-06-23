# League Match History

A command-line tool that fetches and stores a League of Legends player's complete match history using the Riot Games API. Every piece of available data — full participant stats, item purchases, rune selections, challenge metrics, and per-minute timeline events — is stored in a local SQLite database and displayed in a clean terminal UI.

> **Note:** League of Legends and the Riot Games API are trademarks of Riot Games, Inc. This project is not affiliated with or endorsed by Riot Games.

---

## Features

- Fetches every match in a player's history (all queues — ranked, ARAM, normals, Arena, etc.)
- Stores the complete raw match JSON and structured participant stats
- Stores per-minute timeline frames and every in-game event (kills, item purchases, objective kills, ward placements, level-ups, etc.)
- Incremental updates — subsequent fetches only pull matches since the last run
- Name resolution via Data Dragon (item and champion names cached locally)
- Rich terminal output: match list tables, full scoreboards, aggregate stats, timeline summaries

---

## Prerequisites

- Python 3.10+
- A Riot Games API key ([get one at developer.riotgames.com](https://developer.riotgames.com))

---

## Installation

```bash
git clone https://github.com/yourusername/match-history.git
cd match-history

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## Configuration

Copy the example environment file and fill in your API key:

```bash
cp .env.example .env
```

Edit `.env`:

```
RIOT_API_KEY=RGAPI-your-key-here
RIOT_PLATFORM=na1
```

**Platform options:** `na1`, `euw1`, `eune1`, `kr`, `jp1`, `br1`, `la1`, `la2`, `oc1`, `tr1`, `ru`

> **Rate limits:** Development API keys are limited to 20 requests/second and 100 requests/2 minutes. For a player with hundreds of games, the full initial fetch will take several minutes. Subsequent incremental fetches are fast.

---

## Usage

### Fetch matches

```bash
# Fetch only new matches since the last run (incremental — fast on repeat runs)
python main.py fetch "GameName#TAG"

# Fetch the complete history from the beginning of Riot's API records
python main.py full "GameName#TAG"

# Fetch only a specific queue
python main.py fetch "GameName#TAG" --queue ranked
python main.py fetch "GameName#TAG" --queue aram
python main.py fetch "GameName#TAG" --queue 420       # queue ID directly
```

**Queue shorthands:** `ranked`, `ranked-solo`, `ranked-flex`, `flex`, `aram`, `normal`, `blind`, `draft`, `quickplay`, `arena`, `urf`

### Browse match history

```bash
# List most recent matches (20 per page by default)
python main.py view "GameName#TAG"

# Paginate
python main.py view "GameName#TAG" --page 2
python main.py view "GameName#TAG" --page 3 --limit 50

# Filter by queue or champion
python main.py view "GameName#TAG" --queue ranked
python main.py view "GameName#TAG" --champion Karthus
```

### View a match scoreboard

```bash
python main.py match NA1_1234567890
```

Shows the full 10-player scoreboard for the match, including KDA, CS, damage, gold, and all items (resolved to names via Data Dragon).

### Aggregate stats

```bash
python main.py stats "GameName#TAG"
```

Displays overall win rate, KDA, penta/quadra kill totals, breakdown by queue type, and top champions by games played.

### Timeline summary

```bash
python main.py timeline NA1_1234567890
```

Displays a breakdown of all in-game events: total counts by event type, the first 20 kills in chronological order, and the first 20 item purchases.

The full raw timeline JSON (including per-minute participant frames with gold, XP, position, and stats) is stored in the database and accessible via:

```python
import db, json
tl = db.get_timeline("NA1_1234567890")
frames = tl["info"]["frames"]   # list of per-minute frames
```

### Other commands

```bash
# List all players that have been fetched
python main.py players

# Show database summary (match/player/timeline counts)
python main.py dbstats

# Wipe all data (asks for confirmation)
python main.py reset
```

---

## Project Structure

```
match-history/
├── main.py           CLI entry point — all subcommands
├── db.py             SQLite layer — schema, connection, all queries
├── api.py            Async Riot API client (aiohttp, rate-limit aware)
├── ddragon.py        Data Dragon cache — item/champion ID → name
├── display.py        Rich terminal display helpers
├── requirements.txt
├── .env.example      Config template
├── .gitignore
└── LICENSE
```

---

## Database Schema

| Table | Contents |
|-------|----------|
| `players` | One row per tracked player (PUUID, Riot ID, summoner level, last fetch time) |
| `matches` | Match metadata + complete raw `matchDto` JSON |
| `participants` | All scoreboard fields for every player in every stored match |
| `timelines` | Raw `matchTimelineDto` JSON (per-minute frames + all events) |
| `player_matches` | Junction table linking PUUIDs to match IDs |

The raw JSON columns in `matches` and `timelines` preserve the complete API response so no data is ever discarded. The `participants` table extracts ~70 structured fields per player for fast querying without JSON parsing.

---

## Data Stored Per Match

**Match metadata:** game mode, queue, map, patch version, duration, creation timestamp, platform

**Per participant (×10):** champion, role/position, win/loss, KDA, gold earned/spent, CS (minion + jungle), all damage stats (to champions, taken, self-mitigated), vision score, wards placed/killed/bought, objective kills (turrets, inhibitors, baron, dragon), multikill counts, all 6 item slots + trinket, summoner spells + cast counts, ability cast counts, time dead, CC time, first blood/tower flags, all ping counts, rune selections (perks JSON), all challenge metrics (challenges JSON)

**Timeline (per minute):** participant gold, XP, level, position (x/y), current items, ability levels; all events including `CHAMPION_KILL`, `ITEM_PURCHASED`, `ITEM_SOLD`, `ITEM_UNDO`, `WARD_PLACED`, `WARD_KILL`, `BUILDING_KILL`, `ELITE_MONSTER_KILL`, `LEVEL_UP`, `SKILL_LEVEL_UP`, `TURRET_PLATE_DESTROYED`, `GAME_END`, and more

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `aiohttp` | Async HTTP for Riot API requests |
| `python-dotenv` | `.env` file loading |
| `rich` | Terminal tables, panels, and progress bars |

No machine learning or heavy dependencies — pure data collection and display.
