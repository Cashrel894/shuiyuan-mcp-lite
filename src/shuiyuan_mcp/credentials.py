"""Scoped Cookie files, compatible with dajiaohuang/shuiyuan-mcp exports."""

import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

LOGIN_HELP = (
    "请在终端运行 shuiyuan-mcp auth login，按提示粘贴水源登录凭据；"
    "Docker 中请导入登录文件：shuiyuan-mcp auth import <cookies.json>。"
    "也可配置 SHUIYUAN_USER_API_KEY。"
)


class AuthError(Exception):
    """A credential-free, user-facing authentication error."""


class Cookie(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    name: str
    value: str = Field(repr=False)
    domain: str
    path: str = "/"
    expires: float = -1
    secure: bool = True
    httpOnly: bool = True
    sameSite: str = "Lax"


def default_cookie_file() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if not root:
        root = os.environ.get("APPDATA") if os.name == "nt" else str(Path.home() / ".config")
    return Path(root or Path.home()) / "shuiyuan-mcp-lite" / "cookies.json"


def select_cookies(document: object, site: str) -> list[Cookie]:
    """Keep only live cookies for this exact Shuiyuan host and application path."""
    url = urlsplit(site)
    if url.scheme != "https":
        raise AuthError("Cookie 登录只支持 HTTPS 地址。")
    if not isinstance(document, dict) or not isinstance(document.get("cookies"), list):
        raise AuthError("登录文件格式不正确，需要包含 cookies 数组的 JSON 文件。")
    if "site" in document and document["site"] != site:
        raise AuthError("登录文件的站点地址与 SHUIYUAN_BASE_URL 不一致，请重新登录。")
    selected = []
    for item in document["cookies"]:
        # Do not persist jAccount or parent-domain SSO cookies from a storage_state export.
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("domain"), str)
            or item["domain"].lstrip(".") != url.hostname
        ):
            continue
        try:
            cookie = Cookie.model_validate(item)
        except ValidationError:
            raise AuthError("水源 Cookie 字段格式不正确，请重新导出登录文件。") from None
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", cookie.name):
            raise AuthError("Cookie 名称格式不正确。")
        if not cookie.value or any(
            ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in cookie.value
        ):
            raise AuthError("Cookie 内容格式不正确，请重新导出登录文件。")
        if cookie.expires != -1 and cookie.expires <= time.time():
            continue
        # A login cookie must cover the API paths beneath the configured base URL.
        base_path = url.path.rstrip("/") + "/"
        if not cookie.path.startswith("/") or not base_path.startswith(
            cookie.path.rstrip("/") + "/"
        ):
            continue
        selected.append(cookie)
    if not any(c.name == "_t" for c in selected):
        raise AuthError("未找到有效的水源 _t 登录 Cookie，登录可能已过期。" + LOGIN_HELP)
    return selected


def read_document(path: Path) -> object:
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise AuthError("登录文件过大，请仅导出 Cookie 数据。")
        return json.loads(raw)
    except FileNotFoundError:
        raise AuthError("尚未找到登录文件。" + LOGIN_HELP) from None
    except (OSError, ValueError):
        raise AuthError("无法读取登录文件，请检查路径、文件权限和 JSON 格式。") from None


def load_cookies(path: Path, site: str) -> list[Cookie]:
    return select_cookies(read_document(path), site)


def cookie_header(cookies: list[Cookie]) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in sorted(cookies, key=lambda c: -len(c.path)))


def save_cookies(path: Path, site: str, cookies: list[Cookie]) -> None:
    """Atomically replace the credential file with owner-only permissions on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".shuiyuan-auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"site": site, "cookies": [c.model_dump() for c in cookies]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
