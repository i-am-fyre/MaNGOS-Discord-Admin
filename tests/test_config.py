import pytest

from mangosbot.config import ConfigError, parse_config


def minimal(**overrides):
    data = {
        "bot": {"token": "env:DISCORD_TOKEN", "guild_id": 42},
        "database": {"user": "bot", "password": "env:MANGOS_DB_PASSWORD"},
        "realm": [{"id": 1, "name": "Azeroth", "core": "two", "characters_db": "characters", "world_db": "mangos"}],
    }
    for key, value in overrides.items():
        data[key] = value
    return data


def test_example_config_parses(example_config):
    cfg = example_config
    assert cfg.bot.token == "test-token"
    assert cfg.identity.name == "Ask MaNGOS"
    assert cfg.identity.set_nickname and cfg.identity.avatar is None
    assert cfg.identity.color == 0x2ECC71
    assert cfg.auth_database.password == "db-pass"
    assert cfg.realms[0].core == "zero"
    assert cfg.registration.method == "database"
    assert cfg.registration.expansion == 0


def test_env_values_and_core_defaults(env):
    cfg = parse_config(minimal())
    realm = cfg.realms[0]
    assert cfg.bot.token == "test-token"
    assert realm.item_link == "https://www.wowhead.com/wotlk/item={id}"
    assert cfg.registration.expansion == 2  # follows the first realm's core
    assert realm.database.password == "db-pass"  # realms inherit [database]
    assert not realm.soap.enabled


def test_missing_env_var_is_reported(env, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN")
    with pytest.raises(ConfigError, match="DISCORD_TOKEN is not set"):
        parse_config(minimal())


def test_disabled_soap_needs_no_secrets(env, monkeypatch):
    monkeypatch.delenv("SOAP_PASSWORD")
    parse_config(minimal(soap={"enabled": False, "password": "env:SOAP_PASSWORD"}))
    with pytest.raises(ConfigError, match="SOAP_PASSWORD"):
        parse_config(minimal(soap={"enabled": True, "username": "A", "password": "env:SOAP_PASSWORD"}))


def test_validation_errors_are_collected(env):
    data = minimal(
        bot={"token": "x", "guild_id": 0, "colour": 1},
        status={"voice_counter_minutes": 3},
    )
    data["realm"][0]["characters_db"] = "chars; DROP TABLE account"
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    message = str(info.value)
    assert "bot.guild_id is required" in message
    assert "Unknown setting bot.colour" in message
    assert "voice_counter_minutes" in message
    assert "characters_db must contain only" in message


def test_soap_registration_requires_soap(env):
    with pytest.raises(ConfigError, match="SOAP is not enabled"):
        parse_config(minimal(registration={"method": "soap"}))


def test_identity_settings(env, tmp_path):
    avatar = tmp_path / "logo.png"
    avatar.write_bytes(b"fake png bytes")
    cfg = parse_config(minimal(identity={"name": "Ask Stormhold", "avatar": "logo.png", "color": "#123456",
                                         "set_nickname": False}), base_dir=tmp_path)
    assert cfg.identity.name == "Ask Stormhold"
    assert cfg.identity.avatar == avatar  # relative to the config file's folder
    assert cfg.identity.color == 0x123456 and not cfg.identity.set_nickname

    with pytest.raises(ConfigError) as info:
        parse_config(minimal(identity={"name": "x" * 33, "avatar": "missing.png"}), base_dir=tmp_path)
    assert "at most 32" in str(info.value) and "missing.png" in str(info.value)
