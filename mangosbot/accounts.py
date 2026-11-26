"""Game account rules, hashing and creation.

The rules mirror MaNGOS ``AccountMgr``:

* account names and passwords are at most 16 characters (``MAX_ACCOUNT_STR`` /
  ``MAX_PASSWORD_STR``),
* both are upper-cased (Latin letters only) before hashing,
* ``sha_pass_hash = SHA1(UPPER(name) + ":" + UPPER(password))`` as upper-case hex,
* changing a password resets ``v`` and ``s`` so realmd recomputes them on login.

We only accept printable ASCII so Python's upper-casing is identical to the
core's ``Utf8ToUpperOnlyLatin``; non-ASCII passwords are where a hand-rolled
hash would silently diverge from the server and lock the player out.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mangos import Mangos
    from .soap import SoapClient

log = logging.getLogger(__name__)

MAX_ACCOUNT_LENGTH = 16
MAX_PASSWORD_LENGTH = 16
MIN_ACCOUNT_LENGTH = 3

_USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")
# Printable ASCII without space. Quotes/brackets are additionally rejected when
# the password must travel through a SOAP command line.
_PASSWORD_RE = re.compile(r"^[\x21-\x7e]+$")
_SOAP_UNSAFE = set("\"'[]")

# Upper-case letters and digits without look-alikes (0/O, 1/I/L). Game
# passwords are case-insensitive anyway, so lower case adds no strength.
_GENERATED_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class AccountError(Exception):
    """A user-facing problem creating or changing an account."""


class AccountExistsError(AccountError):
    pass


def normalize_username(username: str) -> str:
    return username.strip().upper()


def validate_username(username: str, reserved: frozenset[str] = frozenset()) -> str | None:
    """Return an error message, or None if the (normalised) name is acceptable."""
    if not MIN_ACCOUNT_LENGTH <= len(username) <= MAX_ACCOUNT_LENGTH:
        return f"Account names must be {MIN_ACCOUNT_LENGTH}–{MAX_ACCOUNT_LENGTH} characters."
    if not _USERNAME_RE.match(username):
        return "Account names may only contain letters (A–Z) and numbers."
    if username.upper() in reserved:
        return "That account name is reserved. Please choose another."
    return None


def validate_password(password: str, *, min_length: int, username: str = "", for_soap: bool = False) -> str | None:
    if len(password) < min_length:
        return f"Passwords must be at least {min_length} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Passwords can be at most {MAX_PASSWORD_LENGTH} characters (a limit of the game client)."
    if not _PASSWORD_RE.match(password):
        return "Passwords may use letters, numbers and symbols, but no spaces or accented characters."
    if for_soap and any(ch in _SOAP_UNSAFE for ch in password):
        return "Passwords can't contain quotes or square brackets on this server."
    if username and password.upper() == username.upper():
        return "Your password can't be the same as your account name."
    return None


def _upper_latin(text: str) -> str:
    return "".join(chr(ord(ch) - 32) if "a" <= ch <= "z" else ch for ch in text)


def sha_pass_hash(username: str, password: str) -> str:
    digest = hashlib.sha1(f"{_upper_latin(username)}:{_upper_latin(password)}".encode("utf-8")).hexdigest()
    return digest.upper()


def verify_password(username: str, password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(sha_pass_hash(username, password), (stored_hash or "").upper())


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_GENERATED_ALPHABET) for _ in range(length))


class AccountService:
    """Creates accounts and changes passwords using the configured method.

    ``database`` (default) writes to realmd directly, exactly as AccountMgr does.
    ``soap`` sends ``account create`` to mangosd instead. Note that mangosd logs
    every SOAP command verbatim ("SoapThread: Recieved command ..."), so the
    password ends up in the world server log; that is why it isn't the default.
    """

    def __init__(self, mangos: "Mangos", soap: "SoapClient | None", *, method: str, expansion: int) -> None:
        self._mangos = mangos
        self._soap = soap
        self.method = method
        self.expansion = expansion
        if method == "soap":
            log.warning(
                "registration.method = \"soap\": mangosd writes SOAP commands, including passwords, "
                "to its log. Prefer \"database\" unless you have a reason not to."
            )

    async def exists(self, username: str) -> bool:
        return await self._mangos.account_by_name(username) is not None

    async def create(self, username: str, password: str) -> int:
        username = normalize_username(username)
        if await self.exists(username):
            raise AccountExistsError(username)
        if self.method == "soap":
            if self._soap is None:
                raise AccountError("SOAP is not configured.")
            # "account create NAME PASS EXPANSION" ignores the result on every
            # core, so create first and set the expansion separately.
            await self._soap.execute(f"account create {username} {password}")
            if self.expansion:
                await self._soap.execute(f"account set addon {username} {self.expansion}")
            row = await self._mangos.account_by_name(username)
            if row is None:
                raise AccountError("The server did not create the account.")
            return int(row["id"])
        return await self._mangos.create_account(username, sha_pass_hash(username, password), self.expansion)

    async def change_password(self, account_id: int, username: str, password: str) -> None:
        if self.method == "soap" and self._soap is not None:
            await self._soap.execute(f"account set password {username} {password} {password}")
            return
        await self._mangos.set_password_hash(account_id, sha_pass_hash(username, password))
