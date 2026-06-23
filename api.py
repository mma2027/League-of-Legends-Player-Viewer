from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import aiohttp

# Platform → regional routing cluster
_PLATFORM_TO_REGION: dict[str, str] = {
    "na1":  "americas",
    "br1":  "americas",
    "la1":  "americas",
    "la2":  "americas",
    "euw1": "europe",
    "eune1":"europe",
    "tr1":  "europe",
    "ru":   "europe",
    "kr":   "asia",
    "jp1":  "asia",
    "oc1":  "sea",
    "sg2":  "sea",
    "tw2":  "sea",
    "vn2":  "sea",
}

# Global semaphore — dev key: 20 req/s; leave 2 headroom
_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(18)
    return _SEMAPHORE


class RiotAPIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


@asynccontextmanager
async def make_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    connector = aiohttp.TCPConnector(ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        yield session


class RiotClient:
    def __init__(self, api_key: str, platform: str = "na1",
                 session: Optional[aiohttp.ClientSession] = None):
        self._key = api_key
        self._platform = platform.lower()
        self._region = _PLATFORM_TO_REGION.get(self._platform, "americas")
        self._session = session
        self._headers = {"X-Riot-Token": api_key}

    def _platform_url(self, path: str) -> str:
        return f"https://{self._platform}.api.riotgames.com{path}"

    def _region_url(self, path: str) -> str:
        return f"https://{self._region}.api.riotgames.com{path}"

    async def _get(self, url: str, params: Optional[dict] = None) -> dict | list:
        sem = _get_semaphore()
        own_session = self._session is None
        session = self._session or aiohttp.ClientSession()

        try:
            async with sem:
                async with session.get(url, headers=self._headers,
                                       params=params or {}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", "1"))
                        await asyncio.sleep(retry_after + 1.0)
                        # retry outside semaphore to avoid deadlock
                    elif resp.status == 404:
                        raise RiotAPIError(404, "Not found")
                    elif resp.status == 403:
                        raise RiotAPIError(403, "Forbidden — check API key")
                    elif resp.status == 401:
                        raise RiotAPIError(401, "Unauthorized — invalid API key")
                    else:
                        body = await resp.text()
                        raise RiotAPIError(resp.status, body[:200])

            # Retry after 429 sleep (outside semaphore)
            async with sem:
                async with session.get(url, headers=self._headers,
                                       params=params or {}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    body = await resp.text()
                    raise RiotAPIError(resp.status, body[:200])
        finally:
            if own_session:
                await session.close()

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict:
        """Returns {puuid, gameName, tagLine}."""
        url = self._region_url(
            f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        )
        return await self._get(url)

    # ── Match IDs ─────────────────────────────────────────────────────────────

    async def get_match_ids(self, puuid: str, start: int = 0,
                            count: int = 100,
                            queue: Optional[int] = None,
                            match_type: Optional[str] = None,
                            start_time: Optional[int] = None) -> list[str]:
        """Returns up to `count` match IDs. Queue=None means all queues."""
        url = self._region_url(f"/lol/match/v5/matches/by-puuid/{puuid}/ids")
        params: dict = {"start": start, "count": min(count, 100)}
        if queue is not None:
            params["queue"] = queue
        if match_type is not None:
            params["type"] = match_type
        if start_time is not None:
            params["startTime"] = start_time
        result = await self._get(url, params)
        return result if isinstance(result, list) else []

    async def get_all_match_ids(self, puuid: str,
                                stop_at: Optional[str] = None,
                                queue: Optional[int] = None) -> list[str]:
        """
        Paginate through all match IDs for a player.
        If `stop_at` is given, stops as soon as that match_id is encountered
        (used for incremental fetch — stops at last known match).
        """
        all_ids: list[str] = []
        start = 0
        while True:
            page = await self.get_match_ids(puuid, start=start, count=100, queue=queue)
            if not page:
                break
            for mid in page:
                if stop_at and mid == stop_at:
                    return all_ids
                all_ids.append(mid)
            if len(page) < 100:
                break
            start += len(page)
            await asyncio.sleep(0.1)  # small courtesy delay between pages
        return all_ids

    # ── Match Details ─────────────────────────────────────────────────────────

    async def get_match(self, match_id: str) -> dict:
        url = self._region_url(f"/lol/match/v5/matches/{match_id}")
        return await self._get(url)

    async def get_match_timeline(self, match_id: str) -> dict:
        url = self._region_url(f"/lol/match/v5/matches/{match_id}/timeline")
        return await self._get(url)
