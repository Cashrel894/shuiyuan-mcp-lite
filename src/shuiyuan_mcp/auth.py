"""Explicit user-operated login commands; never run by the MCP tool interface."""

import argparse
import asyncio
import getpass
import sys
import warnings
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from .client import ShuiyuanClient, ShuiyuanError
from .config import Config
from .credentials import AuthError, load_cookies, save_cookies, select_cookies


async def verify(config: Config, cookies=None) -> dict:
    async with aclosing(ShuiyuanClient(config, cookies=cookies)) as client:
        return await client.auth_status()


def prompt_cookies(config: Config):
    if not sys.stdin.isatty():
        raise AuthError("交互登录需要终端；请用 docker exec -it 进入容器，或使用 auth import。")
    print(
        f"1. 在自己的浏览器打开 {config.base_url}，手动完成 jAccount 登录。\n"
        "2. 按 F12 → Application（应用）/ Storage（存储）→ Cookies → 水源域名。\n"
        "3. 复制 _t 的 Value，在下方粘贴；输入不会回显。不要粘贴密码。",
        file=sys.stderr,
    )
    # Never fall back to echoed input if getpass cannot disable terminal echo.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            token = getpass.getpass("水源 _t：", stream=sys.stderr).strip()
            session = getpass.getpass(
                "_forum_session（可选，直接回车跳过）：", stream=sys.stderr
            ).strip()
        except (getpass.GetPassWarning, EOFError):
            raise AuthError("无法安全读取输入；请使用交互终端或 auth import。") from None
    cookies = [{"name": "_t", "value": token, "domain": urlsplit(config.base_url).hostname}]
    if session:
        cookies.append(
            {
                "name": "_forum_session",
                "value": session,
                "domain": urlsplit(config.base_url).hostname,
            }
        )
    return select_cookies({"cookies": cookies}, config.base_url)


async def run_command(args, config: Config) -> None:
    if args.action == "status":
        result = await verify(config)
        print(f"已登录：{result['username']}（{result['method']}）", file=sys.stderr)
    elif args.action == "login":
        cookies = args.cookies
        result = await verify(replace(config, user_api_key="", user_api_client_id=""), cookies)
        await asyncio.to_thread(save_cookies, config.cookie_file, config.base_url, cookies)
        print(f"登录成功：{result['username']}。登录文件：{config.cookie_file}", file=sys.stderr)
        if config.user_api_key:
            print(
                "注意：已配置 User API Key，MCP 将优先使用它；使用 Cookie 请清空该变量。",
                file=sys.stderr,
            )
    elif args.action == "import":
        cookies = await asyncio.to_thread(load_cookies, args.source, config.base_url)
        result = await verify(replace(config, user_api_key="", user_api_client_id=""), cookies)
        await asyncio.to_thread(save_cookies, config.cookie_file, config.base_url, cookies)
        print(f"导入成功：{result['username']}。登录文件：{config.cookie_file}", file=sys.stderr)
        if config.user_api_key:
            print("注意：MCP 将优先使用已配置的 User API Key。", file=sys.stderr)
    elif args.action == "logout":
        await asyncio.to_thread(config.cookie_file.unlink, missing_ok=True)
        print("已删除本地 Cookie 文件；未注销浏览器或撤销服务器会话。", file=sys.stderr)
        if config.user_api_key:
            print("User API Key 仍已配置，若需停止访问请同时清空该环境变量。", file=sys.stderr)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="shuiyuan-mcp auth", description="水源登录与鉴权管理")
    commands = parser.add_subparsers(dest="action", required=True)
    login_parser = commands.add_parser("login", help="引导粘贴水源登录凭据，验证后保存")
    import_parser = commands.add_parser("import", help="验证并导入 Cookie JSON（适合 Docker）")
    import_parser.add_argument(
        "source", type=Path, help="cookies.json 或 Playwright storage state 文件"
    )
    commands.add_parser("status", help="在线检查当前登录态，不显示凭据")
    commands.add_parser("logout", help="删除本地 Cookie 文件")
    for subparser in (
        login_parser,
        import_parser,
        commands.choices["status"],
        commands.choices["logout"],
    ):
        subparser.add_argument(
            "--cookie-file", type=Path, help="登录文件位置；优先于 SHUIYUAN_COOKIE_FILE"
        )
    args = parser.parse_args(argv)
    try:
        config = Config.from_env()
        if args.cookie_file:
            config = replace(config, cookie_file=args.cookie_file.expanduser())
        if args.action == "login":
            args.cookies = prompt_cookies(config)
        asyncio.run(run_command(args, config))
    except (AuthError, ShuiyuanError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("无法读写登录文件，请检查目录和权限。", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("已取消登录操作。", file=sys.stderr)
        raise SystemExit(130) from None
