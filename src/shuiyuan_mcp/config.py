"""Environment configuration and login file location; credentials stay out of repr."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .credentials import default_cookie_file


@dataclass(frozen=True)
class Config:
    base_url: str = "https://shuiyuan.sjtu.edu.cn"
    user_api_key: str = field(default="", repr=False)
    user_api_client_id: str = field(default="", repr=False)

    cookie_file: Path = field(default_factory=default_cookie_file)

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.base_url)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "SHUIYUAN_BASE_URL must be an HTTP(S) URL without credentials/query/fragment"
            )
        for value in (self.user_api_key, self.user_api_client_id):
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ValueError(
                    "User API credentials must contain only printable ASCII characters"
                )
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get("SHUIYUAN_BASE_URL", "https://shuiyuan.sjtu.edu.cn"),
            user_api_key=os.environ.get("SHUIYUAN_USER_API_KEY", ""),
            user_api_client_id=os.environ.get("SHUIYUAN_USER_API_CLIENT_ID", ""),
            cookie_file=Path(os.environ["SHUIYUAN_COOKIE_FILE"]).expanduser()
            if os.environ.get("SHUIYUAN_COOKIE_FILE")
            else default_cookie_file(),
        )
