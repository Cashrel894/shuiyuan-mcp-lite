import pytest

from shuiyuan_mcp.config import Config


def test_environment_config(monkeypatch):
    for key in ("SHUIYUAN_BASE_URL", "SHUIYUAN_USER_API_KEY", "SHUIYUAN_USER_API_CLIENT_ID"):
        monkeypatch.delenv(key, raising=False)
    assert Config.from_env().base_url == "https://shuiyuan.sjtu.edu.cn"
    assert Config.from_env().user_api_key == ""
    monkeypatch.setenv("SHUIYUAN_BASE_URL", "https://shuiyuan.invalid/")
    monkeypatch.setenv("SHUIYUAN_USER_API_KEY", "top-secret")
    monkeypatch.setenv("SHUIYUAN_USER_API_CLIENT_ID", "client-secret")
    config = Config.from_env()
    assert config.base_url == "https://shuiyuan.invalid"
    assert config.user_api_key == "top-secret" and config.user_api_client_id == "client-secret"
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp",
        "https://user:secret@host",
        "https://host?key=secret",
        "https://host/#secret",
        "https://",
        "https://host:invalid",
    ],
)
def test_invalid_base_url(url):
    with pytest.raises(ValueError) as exc:
        Config(base_url=url)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("key", ["secret\n", "secret\r", "secret\x00", "密钥"])
def test_invalid_credential(key):
    with pytest.raises(ValueError) as exc:
        Config(user_api_key=key)
    assert key not in str(exc.value)
