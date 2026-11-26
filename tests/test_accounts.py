from mangosbot import accounts


def test_hash_matches_realmd_default_accounts():
    # Hashes shipped in mangos/Realm_DB for the default accounts.
    assert accounts.sha_pass_hash("ADMINISTRATOR", "ADMINISTRATOR") == "A34B29541B87B7E4823683CE6C7BF6AE68BEAAAC"
    assert accounts.sha_pass_hash("PLAYER", "PLAYER") == "3CE8A96D17C5AE88A30681024E86279F1A38C041"


def test_hash_is_case_insensitive_like_the_core():
    assert accounts.sha_pass_hash("player", "Player") == accounts.sha_pass_hash("PLAYER", "PLAYER")


def test_verify_password_accepts_lowercase_stored_hash():
    assert accounts.verify_password("gamemaster", "gamemaster", "7841e21831d7c6bc0b57fbe7151eb82bd65ea1f9")
    assert not accounts.verify_password("gamemaster", "wrong", "7841e21831d7c6bc0b57fbe7151eb82bd65ea1f9")


def test_username_rules():
    reserved = frozenset({"ADMIN"})
    assert accounts.validate_username("THRALL123", reserved) is None
    assert accounts.validate_username("AB", reserved)  # too short
    assert accounts.validate_username("A" * 17, reserved)  # core limit is 16
    assert accounts.validate_username("BAD NAME", reserved)
    assert accounts.validate_username("ÄBCD", reserved)  # non-ASCII would hash differently
    assert accounts.validate_username("ADMIN", reserved)


def test_password_rules():
    ok = accounts.validate_password
    assert ok("hunter22", min_length=8) is None
    assert ok("short", min_length=8)
    assert ok("x" * 17, min_length=8)  # client can't type more than 16
    assert ok("has space1", min_length=8)
    assert ok("pässword1", min_length=8)
    assert ok('quote"pass', min_length=8, for_soap=True)
    assert ok('quote"pass', min_length=8, for_soap=False) is None
    assert ok("THRALLXX", min_length=8, username="thrallxx")


def test_generated_passwords():
    pw = accounts.generate_password()
    assert len(pw) == 12
    assert accounts.validate_password(pw, min_length=8) is None
    assert pw == pw.upper()
    assert len({accounts.generate_password() for _ in range(50)}) == 50
