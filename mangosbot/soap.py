"""Minimal async client for the mangosd SOAP interface.

mangosd serves gSOAP at ``urn:MaNGOS`` with one operation, ``executeCommand``.
There is no WSDL, so a WSDL-driven client (zeep) can't talk to it; we post the
envelope ourselves. Requests must use HTTP Basic auth with an account of
gmlevel 3 (SEC_ADMINISTRATOR) and commands then run with console privileges, so:

* keep SOAP bound to 127.0.0.1 (the default ``SOAP.IP``), or tunnel to it,
* use a dedicated SOAP account with a strong password,
* never pass unvalidated user input into a command (see ``clean_text``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Callable
from xml.sax.saxutils import escape

import aiohttp

from .config import SoapConfig

log = logging.getLogger(__name__)

_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns1="urn:MaNGOS">'
    "<SOAP-ENV:Body><ns1:executeCommand><command>{command}</command></ns1:executeCommand>"
    "</SOAP-ENV:Body></SOAP-ENV:Envelope>"
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_COLOR_CODE_RE = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")
_MAX_COMMAND = 2000


class SoapError(Exception):
    """The command reached mangosd but failed (the message is the server's output)."""


class SoapAuthError(SoapError):
    """mangosd rejected the SOAP credentials or the account's gmlevel."""


class SoapUnavailable(SoapError):
    """mangosd could not be reached (world server down, SOAP disabled, wrong port)."""


def clean_text(text: str, max_length: int = 255) -> str:
    """Collapse control characters/newlines so text can't smuggle extra arguments."""
    text = _CONTROL_RE.sub(" ", text or "")
    text = re.sub(r" {2,}", " ", text).strip()
    return text[:max_length]


def quote_arg(text: str, max_length: int = 255) -> str:
    """Return ``text`` as a double-quoted command argument (quotes removed)."""
    return '"' + clean_text(text, max_length).replace('"', "'") + '"'


def strip_color_codes(text: str) -> str:
    return _COLOR_CODE_RE.sub("", text or "")


def parse_response(body: str) -> tuple[str | None, str | None]:
    """Return ``(result, fault)`` from a SOAP response body."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SoapError(f"mangosd returned an unreadable response ({exc})") from exc
    result: str | None = None
    fault: str | None = None
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "result":
            result = element.text or ""
        elif tag == "faultstring":
            fault = element.text or ""
    return result, fault


class SoapClient:
    def __init__(self, config: SoapConfig, session: Callable[[], aiohttp.ClientSession]) -> None:
        self._config = config
        self._session = session
        self._auth = aiohttp.encode_basic_auth(config.username, config.password)
        # mangosd handles one SOAP request at a time; queueing here keeps
        # bursts (e.g. a ticket sweep) from timing out on the server side.
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def execute(self, command: str) -> str:
        if not self._config.enabled:
            raise SoapUnavailable("SOAP is disabled in the bot configuration.")
        command = command.strip()
        if not command or len(command) > _MAX_COMMAND or _CONTROL_RE.search(command):
            raise SoapError("Refusing to send an empty, oversized or multi-line command.")
        verb = " ".join(command.split()[:2])  # never log arguments (they may contain passwords)
        payload = _ENVELOPE.format(command=escape(command)).encode("utf-8")
        timeout = aiohttp.ClientTimeout(total=self._config.timeout)
        async with self._lock:
            try:
                async with self._session().post(
                    self._config.url,
                    data=payload,
                    headers={"Content-Type": "text/xml; charset=utf-8", "Authorization": self._auth},
                    timeout=timeout,
                ) as resp:
                    status = resp.status
                    body = await resp.text(errors="replace")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.warning("SOAP %r failed: %s", verb, exc)
                raise SoapUnavailable("Could not reach the world server's SOAP interface.") from exc

        if status == 401:
            raise SoapAuthError("mangosd rejected the SOAP username or password.")
        if status == 403:
            raise SoapAuthError("The SOAP account needs gmlevel 3 (administrator).")
        result, fault = parse_response(body) if body.strip() else (None, None)
        if fault is not None:
            message = strip_color_codes(fault).strip() or "The command failed."
            log.info("SOAP %r returned an error: %s", verb, message.splitlines()[0][:200])
            raise SoapError(message)
        if status >= 400:
            raise SoapError(f"mangosd answered HTTP {status}.")
        log.debug("SOAP %r ok", verb)
        return strip_color_codes(result or "").strip()
