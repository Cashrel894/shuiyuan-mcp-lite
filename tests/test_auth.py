import argparse
import os
from dataclasses import replace

import httpx
import pytest

from shuiyuan_mcp import auth
from shuiyuan_mcp.client import ShuiyuanClient, ShuiyuanError
from shuiyuan_mcp.config import Config
from shuiyuan_mcp.credentials import AuthError, load_cookies, save_cookies, select_cookies

SITE = "https://shuiyuan.invalid"


@pytest.fixture
def credentials(tmp_path):
    config = Config(base_url=SITE, cookie_file=tmp_path / "private" / "cookies.json")
    cookies = select_cookies(
        {
            "cookies": [
                {"name": "_t", "value": "TEST-SECRET", "domain": "shuiyuan.invalid"},
                {"name": "sso", "value": "SSO-SECRET", "domain": "jaccount.sjtu.edu.cn"},
            ]
        },
        SITE,
    )
    return config, cookies


def test_storage_and_scope(credentials):
    config, cookies = credentials
    save_cookies(config.cookie_file, SITE, cookies)
    assert len(load_cookies(config.cookie_file, SITE)) == 1
    assert "SSO-SECRET" not in config.cookie_file.read_text()
    assert "TEST-SECRET" not in repr(cookies)
    if os.name == "posix":
        assert config.cookie_file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(AuthError, match="不一致"):
        load_cookies(config.cookie_file, "https://other.invalid")


@pytest.mark.parametrize(
    "change",
    [
        {"expires": 1.0},
        {"expires": float("nan")},
        {"domain": None},
        {"domain": ".sjtu.edu.cn"},
        {"path": "/other"},
        {"value": "secret\r\nX: value"},
        {"value": "secret;other=value"},
        {"name": "bad name"},
    ],
)
def test_invalid_cookies(change):
    item = {"name": "_t", "value": "TEST-SECRET", "domain": "shuiyuan.invalid", **change}
    with pytest.raises(AuthError) as exc:
        select_cookies({"cookies": [item]}, SITE)
    assert "TEST-SECRET" not in str(exc.value)


async def test_cookie_auth_reload_and_logout(credentials, client_factory, post):
    config, cookies = credentials
    save_cookies(config.cookie_file, SITE, cookies)
    requests = []

    def handler(request):
        requests.append(request)
        assert "User-Api-Key" not in request.headers
        assert request.headers["Cookie"] == "_t=TEST-SECRET"
        if request.url.path == "/session/current.json":
            return httpx.Response(
                200, json={"current_user": {"id": 1, "username": "alice", "email": "private"}}
            )
        return httpx.Response(200, json=post)

    client = client_factory(handler, config)
    assert (await client.auth_status()) == {
        "authenticated": True,
        "username": "alice",
        "method": "cookie",
    }
    await client.read_post(101)
    config.cookie_file.unlink()
    with pytest.raises(ShuiyuanError, match="auth login"):
        await client.read_post(101)
    assert len(requests) == 2


async def test_key_precedence(credentials, client_factory):
    config, _ = credentials

    def handler(request):
        assert request.headers["User-Api-Key"] == "key"
        assert "Cookie" not in request.headers
        return httpx.Response(200, json={"current_user": {"id": 1, "username": "alice"}})

    client = client_factory(handler, replace(config, user_api_key="key"))
    assert (await client.auth_status())["method"] == "user_api_key"


async def test_expired_session(credentials, client_factory):
    config, cookies = credentials
    save_cookies(config.cookie_file, SITE, cookies)
    client = client_factory(lambda r: httpx.Response(200, json={"current_user": None}), config)
    with pytest.raises(ShuiyuanError, match="Authentication Required"):
        await client.auth_status()


@pytest.mark.parametrize("action", ["login", "import"])
async def test_verified_save_and_failed_replacement(credentials, monkeypatch, action, capsys):
    config, cookies = credentials
    source = config.cookie_file.parent.parent / "source.json"
    save_cookies(source, SITE, cookies)

    async def verify(config, cookies=None):
        assert not config.user_api_key
        return {"username": "alice"}

    monkeypatch.setattr(auth, "verify", verify)
    args = argparse.Namespace(action=action, source=source, cookies=cookies)
    await auth.run_command(args, config)
    before = config.cookie_file.read_bytes()

    async def fail(*args):
        raise ShuiyuanError("Authentication Required", "expired")

    monkeypatch.setattr(auth, "verify", fail)
    with pytest.raises(ShuiyuanError):
        await auth.run_command(args, config)
    assert config.cookie_file.read_bytes() == before
    output = capsys.readouterr()
    assert output.out == "" and "TEST-SECRET" not in output.err


def test_prompt_hidden_input(credentials, monkeypatch, capsys):
    config, _ = credentials
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: True)
    answers = iter(["TEST-SECRET", ""])
    monkeypatch.setattr(auth.getpass, "getpass", lambda *a, **kw: next(answers))
    assert auth.prompt_cookies(config)[0].value == "TEST-SECRET"
    output = capsys.readouterr()
    assert not output.out and "TEST-SECRET" not in output.err


def test_noninteractive_login_rejected(credentials, monkeypatch):
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: False)
    with pytest.raises(AuthError, match="交互"):
        auth.prompt_cookies(credentials[0])


async def test_verify_uses_current_session(credentials, monkeypatch):
    config, cookies = credentials

    def factory(config, cookies=None):
        def handler(request):
            assert request.url.path == "/session/current.json"
            assert request.headers["Cookie"] == "_t=TEST-SECRET"
            return httpx.Response(200, json={"current_user": {"id": 1, "username": "alice"}})

        return ShuiyuanClient(config, cookies=cookies, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(auth, "ShuiyuanClient", factory)
    assert (await auth.verify(config, cookies))["username"] == "alice"


def test_bad_json_sanitized(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("TEST-SECRET")
    with pytest.raises(AuthError) as exc:
        load_cookies(path, SITE)
    assert "TEST-SECRET" not in str(exc.value)


async def test_logout(credentials):
    config, cookies = credentials
    save_cookies(config.cookie_file, SITE, cookies)
    await auth.run_command(argparse.Namespace(action="logout"), config)
    assert not config.cookie_file.exists()


def test_cli_missing_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHUIYUAN_COOKIE_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("SHUIYUAN_USER_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        auth.main(["status"])
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert not output.out and "auth login" in output.err and "Traceback" not in output.err


def test_cli_override_logout(credentials, monkeypatch, capsys):
    config, cookies = credentials
    save_cookies(config.cookie_file, SITE, cookies)
    monkeypatch.setenv("SHUIYUAN_COOKIE_FILE", str(config.cookie_file.parent / "unused.json"))
    auth.main(["logout", "--cookie-file", str(config.cookie_file)])
    assert not config.cookie_file.exists()
    output = capsys.readouterr()
    assert not output.out and "TEST-SECRET" not in output.err
