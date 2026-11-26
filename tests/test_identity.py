"""The bot applies [identity]: server nickname, and avatar only when the image changes."""

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mangosbot.bot import ServerBot


async def test_apply_identity(example_config, tmp_path):
    avatar = tmp_path / "logo.png"
    avatar.write_bytes(b"first image")
    config = dataclasses.replace(
        example_config, identity=dataclasses.replace(example_config.identity, name="Ask Stormhold", avatar=avatar)
    )
    bot = ServerBot(config)
    await bot.storage.open()
    try:
        me = SimpleNamespace(nick=None, edit=AsyncMock())
        bot.get_guild = lambda guild_id: SimpleNamespace(me=me)
        user = SimpleNamespace(edit=AsyncMock())
        bot._connection.user = user

        await bot.apply_identity()
        me.edit.assert_awaited_once()
        assert me.edit.await_args.kwargs["nick"] == "Ask Stormhold"
        user.edit.assert_awaited_once_with(avatar=b"first image")

        me.nick = "Ask Stormhold"
        await bot.apply_identity()  # nothing changed: no API calls
        assert me.edit.await_count == 1 and user.edit.await_count == 1

        avatar.write_bytes(b"second image")
        await bot.apply_identity()
        assert user.edit.await_count == 2
    finally:
        bot._connection.user = None
        await bot.close()
