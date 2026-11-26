"""End-to-end flows with fake Discord objects and a fake MaNGOS database."""

import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from mangosbot import accounts
from mangosbot.accounts import AccountService
from mangosbot.cogs.events import EventsCog
from mangosbot.cogs.registration import REGISTER_TRIES_PER_HOUR, RegistrationCog
from mangosbot.storage import Storage

UTC = timezone.utc


class FakeMangos:
    def __init__(self):
        self.accounts: dict[str, dict] = {}
        self.events: list[dict] = []

    async def account_by_name(self, name):
        return self.accounts.get(name.upper())

    async def create_account(self, username, pass_hash, expansion):
        account_id = len(self.accounts) + 1
        self.accounts[username] = {"id": account_id, "username": username, "sha_pass_hash": pass_hash,
                                   "expansion": expansion, "gmlevel": 0}
        return account_id

    async def game_events(self, realm):
        return self.events


class FakeInteraction:
    def __init__(self, user):
        self.user = user
        self.sent: list[discord.Embed] = []
        self._done = False
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: self._done,
                                        send_message=self._send, send_modal=AsyncMock())
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, **kwargs):
        self._done = True

    async def _send(self, *args, embed=None, **kwargs):
        self._done = True
        self.sent.append(embed)


def member(user_id=1, days_old=30):
    m = MagicMock(spec=discord.Member)
    m.id = user_id
    m.roles = []
    m.created_at = discord.utils.utcnow() - timedelta(days=days_old)
    m.joined_at = discord.utils.utcnow() - timedelta(days=1)
    m.mention = f"<@{user_id}>"
    return m


@pytest.fixture
async def fake_bot(example_config, tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    await storage.open()
    mangos = FakeMangos()
    bot = SimpleNamespace(
        config=example_config, storage=storage, mangos=mangos,
        accounts=AccountService(mangos, None, method="database", expansion=0),
        audit=AsyncMock(), add_view=lambda view: None, get_cog=lambda name: None,
        user=None, realm_status={},
    )
    yield bot
    await storage.close()


async def test_registration_flow(fake_bot):
    cog = RegistrationCog(fake_bot)
    user = member()

    inter = FakeInteraction(user)
    await cog.finish_registration(inter, {"username": "thrall", "password": "lok'tar1", "confirm": "lok'tar1"})
    assert "ready" in inter.sent[-1].title
    stored = fake_bot.mangos.accounts["THRALL"]
    assert stored["sha_pass_hash"] == accounts.sha_pass_hash("THRALL", "LOK'TAR1")
    assert [l.username for l in await fake_bot.storage.links_for(user.id)] == ["THRALL"]
    fake_bot.audit.assert_awaited()

    # One account per Discord user by default.
    inter = FakeInteraction(user)
    await cog.finish_registration(inter, {"username": "thrall2", "password": "password1", "confirm": "password1"})
    assert "already have an account" in inter.sent[-1].description


async def test_registration_rejections(fake_bot):
    cog = RegistrationCog(fake_bot)

    young = FakeInteraction(member(user_id=2, days_old=1))
    await cog.finish_registration(young, {"username": "newbie", "password": "password1", "confirm": "password1"})
    assert "days old" in young.sent[-1].description

    # Mistakes in the form don't start any cooldown: fix and resubmit straight away.
    user = member(user_id=3)
    same = FakeInteraction(user)
    await cog.finish_registration(same, {"username": "jainaxyz", "password": "JAINAXYZ", "confirm": "JAINAXYZ"})
    assert "same as your account name" in same.sent[-1].description
    typo = FakeInteraction(user)
    await cog.finish_registration(typo, {"username": "jaina", "password": "password1", "confirm": "password2"})
    assert "don't match" in typo.sent[-1].description
    fixed = FakeInteraction(user)
    await cog.finish_registration(fixed, {"username": "jaina", "password": "password1", "confirm": "password1"})
    assert "ready" in fixed.sent[-1].title

    # Taken names can be retried, but only so many times an hour (stops name probing).
    await fake_bot.mangos.create_account("ARTHAS", "x", 0)
    prober = member(user_id=4)
    form = {"username": "Arthas", "password": "password1", "confirm": "password1"}
    for _ in range(REGISTER_TRIES_PER_HOUR):
        taken = FakeInteraction(prober)
        await cog.finish_registration(taken, form)
        assert "taken" in taken.sent[-1].description
    blocked = FakeInteraction(prober)
    await cog.finish_registration(blocked, form)
    assert "Too many" in blocked.sent[-1].description


async def test_cooldown_between_successful_registrations(fake_bot):
    reg = dataclasses.replace(fake_bot.config.registration, max_accounts_per_user=2)
    fake_bot.config = dataclasses.replace(fake_bot.config, registration=reg)
    cog = RegistrationCog(fake_bot)
    user = member(user_id=5)
    first = FakeInteraction(user)
    await cog.finish_registration(first, {"username": "alt1", "password": "password1", "confirm": "password1"})
    assert "ready" in first.sent[-1].title
    second = FakeInteraction(user)
    await cog.finish_registration(second, {"username": "alt2", "password": "password1", "confirm": "password1"})
    assert "just created an account" in second.sent[-1].description


async def test_event_lifecycle(fake_bot):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    fake_bot.mangos.events = [
        # Running for 1 hour already, lasts 3 days, yearly.
        {"entry": 1, "start_time": (now - timedelta(hours=1)).replace(tzinfo=None), "end_time": datetime(2037, 1, 1),
         "occurence": 525600, "length": 3 * 1440, "holiday": 341, "description": "Midsummer Fire Festival"},
        # Starts in 3 days: should become a Discord scheduled event.
        {"entry": 4, "start_time": (now + timedelta(days=3)).replace(tzinfo=None), "end_time": datetime(2037, 1, 1),
         "occurence": 96480, "length": 10080, "holiday": 374, "description": "Darkmoon Faire"},
        # Not a holiday: ignored with only_holidays = true.
        {"entry": 27, "start_time": datetime(2020, 1, 1), "end_time": datetime(2037, 1, 1),
         "occurence": 1440, "length": 720, "holiday": 0, "description": "Nights"},
    ]
    channel = SimpleNamespace(send=AsyncMock())
    scheduled = SimpleNamespace(id=777, status=discord.ScheduledEventStatus.scheduled, edit=AsyncMock())
    guild = SimpleNamespace(create_scheduled_event=AsyncMock(return_value=scheduled),
                            get_scheduled_event=lambda i: scheduled, fetch_scheduled_event=AsyncMock())
    fake_bot.channel = lambda cid: channel
    fake_bot.guild = guild
    cfg = fake_bot.config
    fake_bot.config = cfg.__class__(**{**cfg.__dict__, "events": cfg.events.__class__(
        **{**cfg.events.__dict__, "timezone": UTC})})

    cog = EventsCog(fake_bot)
    await cog.reload(force=True)
    realm = fake_bot.config.realms[0]

    def texts():
        return [c.kwargs["embed"].description for c in channel.send.await_args_list]

    await cog.process(realm, now)
    assert len(texts()) == 1 and "Midsummer Fire Festival" in texts()[0]
    assert guild.create_scheduled_event.await_count == 1
    assert guild.create_scheduled_event.await_args.kwargs["name"] == "Darkmoon Faire"

    await cog.process(realm, now + timedelta(minutes=5))  # nothing new, no duplicates
    assert len(texts()) == 1 and guild.create_scheduled_event.await_count == 1

    end = now - timedelta(hours=1) + timedelta(days=3)
    await cog.process(realm, end - timedelta(hours=23))
    assert len(texts()) == 2 and "Midsummer" in texts()[1]  # reminder

    await cog.process(realm, end + timedelta(minutes=1))
    assert len(texts()) == 3 and "Midsummer" in texts()[2]  # ended
    assert not any("Nights" in t for t in texts())
