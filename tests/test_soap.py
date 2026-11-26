import base64
import re

import aiohttp
import pytest
from aiohttp import web

from mangosbot.config import SoapConfig
from mangosbot.soap import (SoapAuthError, SoapClient, SoapError, SoapUnavailable, clean_text, parse_response,
                            quote_arg)

RESULT = ('<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
          'xmlns:ns1="urn:MaNGOS"><SOAP-ENV:Body><ns1:executeCommandResponse><result>{}</result>'
          '</ns1:executeCommandResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>')
FAULT = ('<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
         '<SOAP-ENV:Body><SOAP-ENV:Fault><faultcode>SOAP-ENV:Client</faultcode><faultstring>{}</faultstring>'
         '</SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>')


@pytest.fixture
async def soap_server():
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth != "Basic " + base64.b64encode(b"ADMIN:secret").decode():
            return web.Response(status=401)
        body = await request.text()
        command = re.search(r"<command>(.*)</command>", body, re.S).group(1)
        seen.append(command)
        if command.startswith("fail"):
            return web.Response(status=500, text=FAULT.format("Account with this name already exist!"),
                                content_type="text/xml")
        return web.Response(text=RESULT.format("|cff00ff00Players online: 3|r"), content_type="text/xml")

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/", seen
    await runner.cleanup()


def _client(url, session, user="ADMIN", password="secret", enabled=True):
    return SoapClient(SoapConfig(enabled=enabled, url=url, username=user, password=password, timeout=3), lambda: session)


async def test_execute_returns_result_without_color_codes(soap_server):
    url, seen = soap_server
    async with aiohttp.ClientSession() as session:
        out = await _client(url, session).execute("server info")
    assert out == "Players online: 3"
    assert seen == ["server info"]


async def test_command_is_xml_escaped(soap_server):
    url, seen = soap_server
    async with aiohttp.ClientSession() as session:
        await _client(url, session).execute('announce <b>Tom & "Jerry"</b>')
    assert seen[-1] == "announce &lt;b&gt;Tom &amp; \"Jerry\"&lt;/b&gt;"


async def test_fault_raises_with_server_message(soap_server):
    url, _ = soap_server
    async with aiohttp.ClientSession() as session:
        with pytest.raises(SoapError, match="already exist"):
            await _client(url, session).execute("fail please")


async def test_bad_credentials(soap_server):
    url, _ = soap_server
    async with aiohttp.ClientSession() as session:
        with pytest.raises(SoapAuthError):
            await _client(url, session, password="nope").execute("server info")


async def test_unreachable_and_disabled():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(SoapUnavailable):
            await _client("http://127.0.0.1:9/", session).execute("server info")
        with pytest.raises(SoapUnavailable):
            await _client("http://127.0.0.1:9/", session, enabled=False).execute("server info")


async def test_multiline_commands_are_refused():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(SoapError):
            await _client("http://127.0.0.1:9/", session).execute("announce hi\nserver shutdown 0")


def test_text_helpers():
    assert clean_text("hello\nworld\x00!  ") == "hello world !"
    assert clean_text("x" * 300) == "x" * 255
    assert quote_arg('say "hi"\nnow') == "\"say 'hi' now\""


def test_parse_response():
    assert parse_response(RESULT.format("ok")) == ("ok", None)
    assert parse_response(FAULT.format("bad")) == (None, "bad")
    with pytest.raises(SoapError):
        parse_response("not xml")
