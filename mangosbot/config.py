"""Load and validate ``config.toml``.

Any string value may be written as ``"env:NAME"`` to read it from the
environment (or a ``.env`` file). Secrets such as the bot token and database
passwords should always be supplied this way so they never live in a file that
might be committed.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Core name -> account expansion value (0 = Classic ... 4 = Mists of Pandaria).
CORES: dict[str, int] = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4}

# Wowhead "flavour" for each core, used for default item/spell links.
WOWHEAD_FLAVOURS: dict[str, str] = {
    "zero": "classic",
    "one": "tbc",
    "two": "wotlk",
    "three": "cata",
    "four": "mop-classic",
}

DEFAULT_IDENTITY_NAME = "Ask MaNGOS"

_IDENT_RE = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.\-]+:\d{1,5}$")


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbConnection:
    host: str
    port: int
    user: str
    password: str
    pool_size: int

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.host, self.port, self.user)


@dataclass(frozen=True)
class SoapConfig:
    enabled: bool
    url: str
    username: str
    password: str
    timeout: float


@dataclass(frozen=True)
class RealmConfig:
    id: int
    name: str
    core: str
    characters_db: str
    world_db: str
    database: DbConnection
    soap: SoapConfig
    world_address: str | None
    item_link: str
    spell_link: str
    itemset_link: str

    @property
    def expansion(self) -> int:
        return CORES[self.core]


@dataclass(frozen=True)
class BotSettings:
    token: str
    guild_id: int
    data_dir: Path
    log_level: str


@dataclass(frozen=True)
class ServerInfo:
    name: str
    website_url: str
    banner_url: str
    realmlist: str
    client_version: str
    client_download_url: str
    realmd_address: str | None


@dataclass(frozen=True)
class Identity:
    """How the bot presents itself. Neutral by default; admins rebrand it here."""

    name: str  # shown in embed footers and flavour text, and used as the bot's server nickname
    set_nickname: bool
    avatar: Path | None  # optional image uploaded as the bot's avatar when it changes
    color: int  # accent colour for embeds


@dataclass(frozen=True)
class Roles:
    admin: tuple[int, ...]
    staff: tuple[int, ...]
    player: int


@dataclass(frozen=True)
class Channels:
    announcements: int
    status: int
    audit_log: int
    events: int
    tickets_forum: int
    player_count_voice: int


@dataclass(frozen=True)
class RegistrationConfig:
    enabled: bool
    method: str  # "database" | "soap"
    password_mode: str  # "user" | "generated"
    expansion: int
    min_password_length: int
    max_accounts_per_user: int
    min_account_age_days: int
    min_member_age_minutes: int
    cooldown_minutes: int
    global_limit_per_hour: int
    required_role: int
    reserved_names: frozenset[str]
    allow_linking: bool
    allow_gm_accounts: bool


@dataclass(frozen=True)
class StatusConfig:
    enabled: bool
    interval_seconds: int
    presence: bool
    alerts: bool
    down_after_failures: int
    voice_counter_minutes: int
    show_online_names: bool


@dataclass(frozen=True)
class TicketsConfig:
    enabled: bool
    poll_seconds: int
    mention_staff: bool
    auto_archive_minutes: int


@dataclass(frozen=True)
class EventsConfig:
    enabled: bool
    only_holidays: bool
    include: frozenset[int]
    exclude: frozenset[int]
    reminder_hours: int
    start_grace_hours: int
    scheduled_events: bool
    schedule_days_ahead: int
    mention_role: int
    timezone: ZoneInfo | None


@dataclass(frozen=True)
class ArmoryConfig:
    enabled: bool
    privacy: str  # "public" | "opt_in"
    hide_gm_characters: bool
    render_image: bool
    icon_url: str
    dbc_path: str
    icon_field: int | None  # None = auto-detect
    font_path: str


@dataclass(frozen=True)
class LeaderboardConfig:
    enabled: bool
    size: int
    show_gold: bool


@dataclass(frozen=True)
class Config:
    bot: BotSettings
    identity: Identity
    server: ServerInfo
    roles: Roles
    channels: Channels
    auth_database: DbConnection
    realmd_db: str
    registration: RegistrationConfig
    status: StatusConfig
    tickets: TicketsConfig
    events: EventsConfig
    armory: ArmoryConfig
    leaderboard: LeaderboardConfig
    realms: tuple[RealmConfig, ...]

    def realm(self, realm_id: int | None) -> RealmConfig:
        """Return the realm with ``realm_id`` (or the first realm when None)."""
        if realm_id is None:
            return self.realms[0]
        for realm in self.realms:
            if realm.id == realm_id:
                return realm
        raise KeyError(realm_id)

    def realm_by_name(self, name: str | None) -> RealmConfig:
        if not name:
            return self.realms[0]
        for realm in self.realms:
            if realm.name.lower() == name.lower():
                return realm
        raise KeyError(name)


# ---------------------------------------------------------------------------
# Reader helper: typed access with friendly, collected error messages
# ---------------------------------------------------------------------------

_MISSING = object()


class _Section:
    def __init__(self, data: Any, path: str, errors: list[str]) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            errors.append(f"[{path}] must be a table")
            data = {}
        self._data: dict[str, Any] = data
        self._path = path
        self._errors = errors
        self._used: set[str] = set()
        self._env_missing: set[str] = set()

    def _where(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def _raw(self, key: str, default: Any) -> Any:
        self._used.add(key)
        value = self._data.get(key, _MISSING)
        if value is _MISSING:
            return default
        if isinstance(value, str) and value.startswith("env:"):
            var = value[4:].strip()
            env_value = os.environ.get(var)
            if env_value is None:
                self._errors.append(f"{self._where(key)}: environment variable {var} is not set")
                self._env_missing.add(key)
                return default
            return env_value
        return value

    def has(self, key: str) -> bool:
        return key in self._data

    def str(self, key: str, default: str | None = "", *, required: bool = False) -> str:
        value = self._raw(key, _MISSING)
        if value is _MISSING or value is None:
            if required and key not in self._env_missing:
                self._errors.append(f"{self._where(key)} is required")
            return default or ""
        if not isinstance(value, (str, int, float)):
            self._errors.append(f"{self._where(key)} must be a string")
            return default or ""
        return str(value).strip()

    def int(self, key: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        value = self._raw(key, default)
        try:
            number = int(value)
        except (TypeError, ValueError):
            self._errors.append(f"{self._where(key)} must be a whole number")
            return default
        if isinstance(value, bool):
            self._errors.append(f"{self._where(key)} must be a whole number")
            return default
        if minimum is not None and number < minimum:
            self._errors.append(f"{self._where(key)} must be at least {minimum}")
        if maximum is not None and number > maximum:
            self._errors.append(f"{self._where(key)} must be at most {maximum}")
        return number

    def float(self, key: str, default: float, *, minimum: float | None = None) -> float:
        value = self._raw(key, default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            self._errors.append(f"{self._where(key)} must be a number")
            return default
        if minimum is not None and number < minimum:
            self._errors.append(f"{self._where(key)} must be at least {minimum}")
        return number

    def bool(self, key: str, default: bool) -> bool:
        value = self._raw(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "yes", "on", "1", "false", "no", "off", "0"}:
            return value.lower() in {"true", "yes", "on", "1"}
        self._errors.append(f"{self._where(key)} must be true or false")
        return default

    def choice(self, key: str, default: str, choices: tuple[str, ...]) -> str:
        value = self.str(key, default).lower()
        if value not in choices:
            self._errors.append(f"{self._where(key)} must be one of: {', '.join(choices)}")
            return default
        return value

    def int_list(self, key: str) -> tuple[int, ...]:
        value = self._raw(key, [])
        if isinstance(value, int) and not isinstance(value, bool):
            value = [value]
        if not isinstance(value, list):
            self._errors.append(f"{self._where(key)} must be a list of numbers")
            return ()
        out: list[int] = []
        for item in value:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                self._errors.append(f"{self._where(key)} contains a non-number: {item!r}")
        return tuple(i for i in out if i)

    def str_list(self, key: str, default: list[str]) -> tuple[str, ...]:
        value = self._raw(key, default)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self._errors.append(f"{self._where(key)} must be a list of strings")
            return tuple(default)
        return tuple(v.strip() for v in value if v.strip())

    def section(self, key: str) -> "_Section":
        self._used.add(key)
        return _Section(self._data.get(key), self._where(key), self._errors)

    def raw_table(self, key: str) -> dict[str, Any]:
        self._used.add(key)
        value = self._data.get(key) or {}
        return value if isinstance(value, dict) else {}

    def unknown_keys(self) -> list[str]:
        return [self._where(k) for k in self._data if k not in self._used]


def _ident(section: _Section, key: str, default: str, errors: list[str]) -> str:
    value = section.str(key, default)
    if not _IDENT_RE.match(value):
        errors.append(f"{section._where(key)} must contain only letters, digits and underscores")
    return value


def _address(section: _Section, key: str, errors: list[str]) -> str | None:
    value = section.str(key, "")
    if not value:
        return None
    if not _ADDRESS_RE.match(value):
        errors.append(f"{section._where(key)} must look like host:port")
        return None
    return value


def _color(section: _Section, key: str, default: int, errors: list[str]) -> int:
    value = section.str(key, "")
    if not value:
        return default
    try:
        return int(value.lstrip("#"), 16)
    except ValueError:
        errors.append(f"{section._where(key)} must be a hex colour such as \"#2ECC71\"")
        return default


def _db_connection(section: _Section, base: DbConnection | None) -> DbConnection:
    return DbConnection(
        host=section.str("host", base.host if base else "127.0.0.1"),
        port=section.int("port", base.port if base else 3306, minimum=1, maximum=65535),
        user=section.str("user", base.user if base else "", required=base is None),
        password=section.str("password", base.password if base else ""),
        pool_size=section.int("pool_size", base.pool_size if base else 5, minimum=1, maximum=50),
    )


def _soap_config(section: _Section, base: SoapConfig | None, errors: list[str]) -> SoapConfig:
    enabled = section.bool("enabled", base.enabled if base else False)
    if not enabled:
        # Don't require SOAP_* environment variables for a disabled feature.
        section._used.update({"url", "username", "password", "timeout"})
        return SoapConfig(enabled=False, url=base.url if base else "http://127.0.0.1:7878/",
                          username="", password="", timeout=base.timeout if base else 10.0)
    url = section.str("url", base.url if base else "http://127.0.0.1:7878/")
    username = section.str("username", base.username if base else "")
    password = section.str("password", base.password if base else "")
    timeout = section.float("timeout", base.timeout if base else 10.0, minimum=1.0)
    if enabled:
        if not url.startswith(("http://", "https://")):
            errors.append(f"{section._where('url')} must start with http:// or https://")
        if not username or not password:
            errors.append(f"{section._path}: username and password are required when SOAP is enabled")
    return SoapConfig(enabled=enabled, url=url, username=username, password=password, timeout=timeout)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"Config file {path} not found. Copy config.example.toml to {path.name} and edit it."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    return parse_config(data, base_dir=path.parent)


def parse_config(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    errors: list[str] = []
    root = _Section(data, "", errors)
    base_dir = base_dir or Path.cwd()

    # [bot]
    b = root.section("bot")
    token = b.str("token", required=True)
    guild_id = b.int("guild_id", 0, minimum=0)
    if guild_id <= 0:
        errors.append("bot.guild_id is required (right-click your Discord server > Copy Server ID)")
    data_dir = Path(b.str("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = base_dir / data_dir
    log_level = b.choice("log_level", "info", ("debug", "info", "warning", "error")).upper()
    bot = BotSettings(token=token, guild_id=guild_id, data_dir=data_dir, log_level=log_level)

    # [identity]
    i = root.section("identity")
    identity_name = i.str("name", DEFAULT_IDENTITY_NAME) or DEFAULT_IDENTITY_NAME
    if len(identity_name) > 32:
        errors.append("identity.name can be at most 32 characters (Discord's nickname limit)")
    avatar_raw = i.str("avatar", "")
    avatar: Path | None = None
    if avatar_raw:
        avatar = Path(avatar_raw)
        if not avatar.is_absolute():
            avatar = base_dir / avatar
        if avatar.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            errors.append("identity.avatar must be a .png, .jpg, .gif or .webp image")
        elif not avatar.is_file():
            errors.append(f"identity.avatar: {avatar} not found")
        elif avatar.stat().st_size > 8 * 1024 * 1024:
            errors.append("identity.avatar must be smaller than 8 MB")
    identity = Identity(
        name=identity_name,
        set_nickname=i.bool("set_nickname", True),
        avatar=avatar,
        color=_color(i, "color", 0x2ECC71, errors),
    )

    # [server]
    s = root.section("server")
    server = ServerInfo(
        name=s.str("name", "MaNGOS Server"),
        website_url=s.str("website_url", ""),
        banner_url=s.str("banner_url", ""),
        realmlist=s.str("realmlist", ""),
        client_version=s.str("client_version", ""),
        client_download_url=s.str("client_download_url", ""),
        realmd_address=_address(s, "realmd_address", errors),
    )
    for key, url in (("website_url", server.website_url), ("banner_url", server.banner_url),
                     ("client_download_url", server.client_download_url)):
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"server.{key} must start with http:// or https://")

    # [roles]
    r = root.section("roles")
    roles = Roles(admin=r.int_list("admin"), staff=r.int_list("staff"), player=r.int("player", 0, minimum=0))

    # [channels]
    c = root.section("channels")
    channels = Channels(
        announcements=c.int("announcements", 0, minimum=0),
        status=c.int("status", 0, minimum=0),
        audit_log=c.int("audit_log", 0, minimum=0),
        events=c.int("events", 0, minimum=0),
        tickets_forum=c.int("tickets_forum", 0, minimum=0),
        player_count_voice=c.int("player_count_voice", 0, minimum=0),
    )

    # [database] (realmd / login database)
    d = root.section("database")
    auth_db = _db_connection(d, None)
    realmd_db = _ident(d, "realmd", "realmd", errors)

    # [soap] defaults shared by all realms
    soap_default = _soap_config(root.section("soap"), None, errors)

    # [[realm]]
    realms: list[RealmConfig] = []
    raw_realms = data.get("realm")
    root._used.add("realm")
    if isinstance(raw_realms, dict):
        raw_realms = [raw_realms]
    if not isinstance(raw_realms, list) or not raw_realms:
        errors.append("At least one [[realm]] section is required")
        raw_realms = []
    seen_ids: set[int] = set()
    for index, raw in enumerate(raw_realms):
        rs = _Section(raw, f"realm[{index}]", errors)
        realm_id = rs.int("id", 1, minimum=1)
        if realm_id in seen_ids:
            errors.append(f"realm[{index}].id {realm_id} is used by more than one realm")
        seen_ids.add(realm_id)
        core = rs.choice("core", "zero", tuple(CORES))
        flavour = WOWHEAD_FLAVOURS[core]
        realm_db = _db_connection(rs.section("database"), auth_db)
        realm_soap = _soap_config(rs.section("soap"), soap_default, errors)
        item_link = rs.str("item_link", f"https://www.wowhead.com/{flavour}/item={{id}}")
        spell_link = rs.str("spell_link", f"https://www.wowhead.com/{flavour}/spell={{id}}")
        itemset_link = rs.str("itemset_link", f"https://www.wowhead.com/{flavour}/item-set={{id}}")
        for key, tpl in (("item_link", item_link), ("spell_link", spell_link), ("itemset_link", itemset_link)):
            if tpl and "{id}" not in tpl:
                errors.append(f"realm[{index}].{key} must contain {{id}}")
        realms.append(
            RealmConfig(
                id=realm_id,
                name=rs.str("name", f"Realm {realm_id}"),
                core=core,
                characters_db=_ident(rs, "characters_db", "character0", errors),
                world_db=_ident(rs, "world_db", "mangos0", errors),
                database=realm_db,
                soap=realm_soap,
                world_address=_address(rs, "world_address", errors),
                item_link=item_link,
                spell_link=spell_link,
                itemset_link=itemset_link,
            )
        )
        for key in rs.unknown_keys():
            errors.append(f"Unknown setting {key}")

    # [registration]
    g = root.section("registration")
    default_expansion = CORES[realms[0].core] if realms else 0
    registration = RegistrationConfig(
        enabled=g.bool("enabled", True),
        method=g.choice("method", "database", ("database", "soap")),
        password_mode=g.choice("password_mode", "user", ("user", "generated")),
        expansion=g.int("expansion", default_expansion, minimum=0, maximum=4),
        min_password_length=g.int("min_password_length", 8, minimum=4, maximum=16),
        max_accounts_per_user=g.int("max_accounts_per_user", 1, minimum=1, maximum=25),
        min_account_age_days=g.int("min_account_age_days", 7, minimum=0),
        min_member_age_minutes=g.int("min_member_age_minutes", 10, minimum=0),
        cooldown_minutes=g.int("cooldown_minutes", 5, minimum=0),
        global_limit_per_hour=g.int("global_limit_per_hour", 30, minimum=1),
        required_role=g.int("required_role", 0, minimum=0),
        reserved_names=frozenset(
            n.upper()
            for n in g.str_list(
                "reserved_names",
                ["ADMIN", "ADMINISTRATOR", "GM", "GAMEMASTER", "MODERATOR", "STAFF", "SYSTEM", "SERVER", "SUPPORT"],
            )
        ),
        allow_linking=g.bool("allow_linking", True),
        allow_gm_accounts=g.bool("allow_gm_accounts", False),
    )
    if registration.enabled and registration.method == "soap" and not any(r.soap.enabled for r in realms):
        errors.append("registration.method is \"soap\" but SOAP is not enabled")

    # [status]
    st = root.section("status")
    voice_minutes = st.int("voice_counter_minutes", 10, minimum=0)
    if 0 < voice_minutes < 10:
        errors.append("status.voice_counter_minutes must be 0 (off) or at least 10 (Discord rate limit)")
    status = StatusConfig(
        enabled=st.bool("enabled", True),
        interval_seconds=st.int("interval_seconds", 60, minimum=15),
        presence=st.bool("presence", True),
        alerts=st.bool("alerts", True),
        down_after_failures=st.int("down_after_failures", 2, minimum=1, maximum=20),
        voice_counter_minutes=voice_minutes,
        show_online_names=st.bool("show_online_names", False),
    )

    # [tickets]
    t = root.section("tickets")
    archive = t.int("auto_archive_minutes", 4320)
    if archive not in (60, 1440, 4320, 10080):
        errors.append("tickets.auto_archive_minutes must be 60, 1440, 4320 or 10080")
        archive = 4320
    tickets = TicketsConfig(
        enabled=t.bool("enabled", True),
        poll_seconds=t.int("poll_seconds", 60, minimum=15),
        mention_staff=t.bool("mention_staff", True),
        auto_archive_minutes=archive,
    )

    # [events]
    e = root.section("events")
    tz_name = e.str("timezone", "")
    tz: ZoneInfo | None = None
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"events.timezone {tz_name!r} is not a valid IANA time zone (e.g. \"Europe/London\")")
    events = EventsConfig(
        enabled=e.bool("enabled", True),
        only_holidays=e.bool("only_holidays", True),
        include=frozenset(e.int_list("include")),
        exclude=frozenset(e.int_list("exclude")),
        reminder_hours=e.int("reminder_hours", 24, minimum=0),
        start_grace_hours=e.int("start_grace_hours", 24, minimum=0),
        scheduled_events=e.bool("discord_scheduled_events", True),
        schedule_days_ahead=e.int("schedule_days_ahead", 14, minimum=1, maximum=90),
        mention_role=e.int("mention_role", 0, minimum=0),
        timezone=tz,
    )

    # [armory]
    a = root.section("armory")
    icon_field_raw = a.str("icon_field", "auto")
    icon_field: int | None = None
    if icon_field_raw.lower() != "auto":
        try:
            icon_field = int(icon_field_raw)
        except ValueError:
            errors.append("armory.icon_field must be \"auto\" or a field number")
    icon_url = a.str("icon_url", "https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg")
    if icon_url and "{icon}" not in icon_url:
        errors.append("armory.icon_url must contain {icon}")
    armory = ArmoryConfig(
        enabled=a.bool("enabled", True),
        privacy=a.choice("privacy", "public", ("public", "opt_in")),
        hide_gm_characters=a.bool("hide_gm_characters", True),
        render_image=a.bool("render_image", True),
        icon_url=icon_url,
        dbc_path=a.str("dbc_path", ""),
        icon_field=icon_field,
        font_path=a.str("font_path", ""),
    )

    # [leaderboard]
    lb = root.section("leaderboard")
    leaderboard = LeaderboardConfig(
        enabled=lb.bool("enabled", True),
        size=lb.int("size", 10, minimum=3, maximum=25),
        show_gold=lb.bool("show_gold", True),
    )

    for key in root.unknown_keys():
        errors.append(f"Unknown section or setting {key}")
    for sec in (b, i, s, r, c, d, g, st, t, e, a, lb):
        for key in sec.unknown_keys():
            errors.append(f"Unknown setting {key}")

    if errors:
        unique = list(dict.fromkeys(errors))  # realms inherit [soap]/[database], avoid repeats
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(unique))

    return Config(
        bot=bot,
        identity=identity,
        server=server,
        roles=roles,
        channels=channels,
        auth_database=auth_db,
        realmd_db=realmd_db,
        registration=registration,
        status=status,
        tickets=tickets,
        events=events,
        armory=armory,
        leaderboard=leaderboard,
        realms=tuple(realms),
    )
