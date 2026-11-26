"""Who may appear in the armory, leaderboards and online lists.

* ``armory.privacy = "public"``: everyone is shown unless their linked Discord
  user chose ``/account privacy visible:False``.
* ``armory.privacy = "opt_in"``: only accounts whose linked Discord user chose
  ``visible:True`` are shown.
* GM accounts (gmlevel > 0) are hidden when ``armory.hide_gm_characters``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import ServerBot

_TTL = 60.0


class Visibility:
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self._cached: tuple[float, set[int], set[int] | None] | None = None

    def invalidate(self) -> None:
        self._cached = None

    async def filters(self) -> tuple[set[int], set[int] | None]:
        """Return ``(excluded_account_ids, only_these_account_ids_or_None)``."""
        now = time.monotonic()
        if self._cached and now - self._cached[0] < _TTL:
            return self._cached[1], self._cached[2]
        cfg = self.bot.config.armory
        excluded: set[int] = set()
        if cfg.hide_gm_characters:
            excluded |= await self.bot.mangos.gm_account_ids()
        only: set[int] | None = None
        if cfg.privacy == "opt_in":
            only = await self.bot.storage.account_ids_with_visibility(True)
        else:
            excluded |= await self.bot.storage.account_ids_with_visibility(False)
        self._cached = (now, excluded, only)
        return excluded, only

    async def can_show(self, account_id: int) -> bool:
        excluded, only = await self.filters()
        if account_id in excluded:
            return False
        return only is None or account_id in only
