# shuiyuan-mcp-lite

面向上海交通大学水源社区的轻量级、只读 **标准 MCP Server**。使用 Python、官方 MCP Python SDK 和异步 httpx，通过 stdio 接入 AstrBot 或其他 MCP Host，无 AstrBot 专用依赖。

## 安装与启动

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在仓库目录执行：

```bash
uv sync
uv run shuiyuan-mcp
```

首次使用先运行 `uv run shuiyuan-mcp auth login` 完成鉴权配置。

启动后等待 Host 通过 stdin 发送 MCP 消息，无交互提示，也不会主动请求水源。stdout 仅用于 MCP 协议；日志写入 stderr。直接在终端运行时没有输出属于正常情况。Host 关闭输入后进程退出。

依赖版本记录在 `uv.lock`。部署可执行 `uv sync --locked --no-dev`；安装后的 CLI 名称为 `shuiyuan-mcp`。当前锁定官方 SDK 的 1.x 维护版本（`mcp>=1.26,<2`），不依赖独立的 `fastmcp` 包。

## 配置

服务配置来自环境变量；Cookie 凭据存储在登录文件中，不自动加载 `.env` 文件。水源帖子需要登录权限，未配置有效凭据时工具会提示先登录，不再尝试匿名读取。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SHUIYUAN_BASE_URL` | `https://shuiyuan.sjtu.edu.cn` | 水源部署的 HTTP(S) 地址，不可含用户名、密码、查询参数或 fragment |
| `SHUIYUAN_USER_API_KEY` | 空 | 可选 Discourse User API Key；配置后优先于 Cookie 文件 |
| `SHUIYUAN_USER_API_CLIENT_ID` | 空 | 可选客户端 ID，仅配置 key 时发送 |
| `SHUIYUAN_COOKIE_FILE` | `~/.config/shuiyuan-mcp-lite/cookies.json`（Linux） | 用户提供的水源登录文件；支持 XDG_CONFIG_HOME，Windows 使用 APPDATA |

配置 key 后通过 `User-Api-Key` 请求头鉴权，配置 client ID 后同时发送 `User-Api-Client-Id`。请使用具有所需读取权限的 key；内容可见性仍受站点权限控制。登录失效时会提示重新登录，不会自动操作 jAccount。

本项目不保存 jAccount 密码、不自动读取浏览器 Cookie、不硬编码或记录完整凭据。

### 首次登录（推荐，容器也可用）

```bash
uv run shuiyuan-mcp auth login
uv run shuiyuan-mcp auth status
```

登录命令提供中文步骤：在自己的浏览器手动登录水源，按 F12 打开开发者工具，在 Application（应用）或 Storage（存储）的 Cookies 下选择水源域名，复制 `_t` 的 Value，粘贴到终端。还可输入 `_forum_session`（可选）。输入不回显，也不放入命令行参数；不要将 Cookie 发给聊天机器人。

命令通过 `GET /session/current.json` 验证身份，成功后才保存登录文件；失败不会覆盖旧文件。文件使用原子替换，POSIX 权限为 0600。Cookie 文件是明文凭据，请仅保存在自己的持久化目录。登录过期后再次运行同一命令。

### 导入已有登录文件

兼容 `dajiaohuang/shuiyuan-mcp` 的 `cookies.json`（`{site, cookies}`）以及用户主动导出的 Playwright storage state（`{cookies, origins}`）：

```bash
uv run shuiyuan-mcp auth import /path/to/exported-cookies.json
uv run shuiyuan-mcp auth status
uv run shuiyuan-mcp auth logout
```

只保存当前水源域名、适用路径、尚未过期的 Cookie，丢弃 jAccount、父域 SSO 和 localStorage 数据。Cookie 鉴权只支持 HTTPS。`logout` 仅删除本地文件，不会注销浏览器或撤销服务器会话；若配置了 User API Key，还需清空相应环境变量。

所有鉴权子命令支持 `--cookie-file /absolute/path/cookies.json`，优先于环境变量；服务端仍需用 `SHUIYUAN_COOKIE_FILE` 指向同一位置。文件更新或删除将在下一次请求生效。未登录也能启动 MCP、列出工具，但读取内容会返回明确的登录提示。凭据不通过 MCP 工具输入或返回。


## MCP Tools

所有工具都是只读操作，返回经过校验和字段筛选的 JSON，同时提供 MCP 文本和结构化结果。

| 工具 | 参数 | 返回内容 |
| --- | --- | --- |
| `search` | `keyword`、`username`、`category`、`before`、`after`、`order`、`status`、`page`、`offset`、`limit`、`max_chars` | 帖子/主题 ID、主题标题、用户名、时间、精简摘要、链接和分页信息 |
| `read_topic` | `topic_id`、`offset`、`limit`、`max_chars` | 主题 ID、标题、分类、标签、回复及回复关系、分页信息 |
| `read_post` | `post_id`、`offset`、`max_chars` | 原始 Markdown、主题/帖子 ID、用户名、时间、回复关系、retorts、polls |
| `get_user` | `username`、`max_chars` | 公开资料白名单：ID、用户名、名称、头衔、注册时间、信任等级、简介、位置、个人网站和资料链接 |
| `list_user_posts` | `username`、`offset`、`limit`、`max_chars` | 用户最近发帖/回复的 ID、标题、摘要、时间、回复关系和链接 |

`get_user` 不返回 email、IP、会话信息或用户自定义字段。简介和帖子内容属于用户发布的内容，Host 应将其视为待阅读的数据。

### 搜索与分页

- `keyword` 可包含 Discourse 原生搜索语法，也可留空并提供其他筛选条件。最长 1000 字符。
- `username` 转换为 `user:...`；`category` 接受分类 ID 或 slug（可用 `parent/child`）。
- `before`、`after` 使用 `YYYY-MM-DD`，分别转换为日期筛选；同时提供时 `after` 必须早于 `before`。
- `order`：`relevance`、`latest`、`oldest`、`latest_topic`、`oldest_topic`、`views`、`likes`。
- `status`：`open`、`closed`、`archived`、`noreplies`、`single_user`。
- `page` 是上游搜索页，从 1 开始，最多 10；`offset` 是该页内从 0 开始的位置。先消费 `next_offset`，没有后再用 `next_page` 并将 offset 重置为 0，避免遗漏该页结果。站点可能限制可搜索的范围和关键词长度。
- `read_topic.offset` 是可见帖子 ID 流中的索引，**不是楼层号**。只补取所选窗口中尚未加载的帖子，每批最多 20 个，不遍历整个主题。
- `list_user_posts.offset` 是用户活动偏移，使用 `filter=4,5`。满页时返回 `next_offset`，下一页可能为空。
- `read_post.offset` 和 `next_offset` 是原始 Markdown 的字符位置，可继续读取被截断的正文。没有下一页时 `next_offset` / `next_page` 为 `null`。

### 输出限制

| 工具 | 默认 limit / 上限 | 默认 max_chars / 上限 |
| --- | --- | --- |
| `search` | 20 / 50 | 每条摘要 500 / 2000 |
| `read_topic` | 10 / 50 | 每条正文 2000 / 10000 |
| `read_post` | 不适用 | 正文 8000 / 20000 |
| `get_user` | 不适用 | 简介 2000 / 10000 |
| `list_user_posts` | 20 / 50 | 每条摘要 500 / 2000 |

超出参数范围会报 `Invalid Argument` 或 MCP 参数校验错误。正文/摘要截断用 `truncated` 标明。主题正文优先使用 raw Markdown，没有 raw 时从 API 提供的 cooked HTML 提取纯文本，`content_format` 表示实际格式；不抓取 HTML 页面。

`read_post` 中缺失或为 null 的 `retorts`、`polls` 返回空列表，不依赖 `polls_votes`。retorts 仅包含 `emoji`、`usernames`；polls 仅包含 `title`、`options[{text,votes}]`。上游隐藏票数时 `votes` 为 `null`，不会伪装成 0。

单帖插件元数据最多返回 50 种 retort、每种 100 个用户名、10 个投票、每个投票 100 个选项；emoji、投票标题和选项文本分别最多 100、300、500 字符。裁剪时设置 `metadata_truncated`。其他标题、个人资料短文本和资源描述也有固定长度限制。

## MCP Resources

| URI | API | 内容 |
| --- | --- | --- |
| `shuiyuan://categories` | `GET /site.json` | 可见分类 ID、名称、slug、父分类、简短描述 |
| `shuiyuan://tags` | `GET /tags.json` | 可见标签 ID、名称、主题计数；合并分组标签并去重 |

资源 MIME 类型为 `application/json`，保持只读。分类最多 500 条、标签最多 1000 条，返回 `total` 与 `truncated`；不返回原始站点配置。这两个固定资源不提供分页，`total` 指本次上游响应中可见的数量。

## AstrBot 配置

先将项目放到 AstrBot 能访问的目录并运行 `uv sync`，再填写 stdio MCP 配置：

```json
{
  "command": "uv",
  "args": [
    "--directory",
    "/AstrBot/data/mcp/shuiyuan-mcp",
    "run",
    "shuiyuan-mcp"
  ]
}
```

将目录替换为实际绝对路径。AstrBot 进程需要能在 PATH 中找到 uv，否则将 `command` 改成 uv 的绝对路径。通过 Host 的环境变量配置向子进程传入登录文件路径，或可选的 key 和 client ID。其他支持 stdio 的 MCP Host 可复用相同 command/args。

### Docker 容器内克隆

在运行 AstrBot 的容器内执行以下命令（需要 git、uv 和 Python 3.12+）：

```bash
mkdir -p /AstrBot/data/mcp
git clone https://github.com/Cashrel894/shuiyuan-mcp-lite.git /AstrBot/data/mcp/shuiyuan-mcp-lite
cd /AstrBot/data/mcp/shuiyuan-mcp-lite
uv sync --locked --no-dev
```

也可以从宿主机先用 `docker exec -it <容器名> sh` 进入容器，再执行上述命令。确认 `/AstrBot/data` 对应实际的持久化挂载目录，且 AstrBot 运行用户对项目目录有读写权限。

在容器交互终端中完成登录，保存到持久化目录：

```bash
export SHUIYUAN_COOKIE_FILE=/AstrBot/data/shuiyuan-auth/cookies.json
.venv/bin/shuiyuan-mcp auth login
.venv/bin/shuiyuan-mcp auth status
```

没有交互终端时，可将自己导出的 Cookie 文件复制到容器，再执行 `auth import /path/to/exported-cookies.json`。不要把凭据提交到仓库。

安装后直接使用虚拟环境中的入口，并明确配置登录文件：

```json
{
  "command": "/AstrBot/data/mcp/shuiyuan-mcp-lite/.venv/bin/shuiyuan-mcp",
  "args": [],
  "env": {
    "SHUIYUAN_COOKIE_FILE": "/AstrBot/data/shuiyuan-auth/cookies.json"
  }
}
```

这是 AstrBot 所在容器内的路径。虚拟环境应在该容器中创建；不要复制宿主机的 `.venv`。更换容器基础镜像或 Python 后应重新创建虚拟环境并安装依赖。无需新增端口映射，AstrBot 会启动 stdio 子进程。

更新代码时在仓库目录执行 `git pull --ff-only` 和 `uv sync --locked --no-dev`，然后在 AstrBot 中重新连接此 MCP Server。

## 错误和网络行为

区分 `Not Found`（404）、`Authentication Required`（401 / 登录重定向）、`Permission Denied`（403）、`Rate Limited`（429）、`Shuiyuan Unreachable`（连接故障 / 5xx）、`Timeout`、`Unexpected API Response`（意外状态码 / JSON / schema）。工具错误作为 MCP `isError=true` 返回；资源错误作为 MCP 错误返回。错误不附带上游响应正文或完整 Python traceback。

HTTP 客户端最多 4 个连接、4 个并发请求，连接超时 10 秒，其他 HTTP 阶段超时 20 秒；不自动重试、不跟随重定向。遇到限流应等待后再由 Host 重试。

## 开发与验证

```bash
uv sync --locked
uv run ruff format
uv run ruff check
uv run pytest
```

测试使用 `httpx.MockTransport`，阻止测试进程中的真实 HTTP 请求。覆盖查询构造、数据精简、主题补页、插件字段缺失、鉴权头、敏感字段过滤、并发限制、错误映射、MCP 工具/资源调用，以及通过 `uv run --offline shuiyuan-mcp` 启动子进程后的真实 stdio 握手。CLI 测试只列出能力并提交无效参数，不访问真实水源。

- `src/shuiyuan_mcp/server.py`：工具、资源和 CLI。
- `src/shuiyuan_mcp/client.py`：异步 HTTP、分页、结果精简和错误映射。
- `src/shuiyuan_mcp/models.py`：上游响应校验、API HTML 的纯文本转换。
- `src/shuiyuan_mcp/config.py`：环境变量配置。
- `src/shuiyuan_mcp/auth.py`、`credentials.py`：交互鉴权、在线检查、登录文件导入和保存。
- `tests/`：mock 和协议测试。

开发遵循 [AGENTS.md](AGENTS.md) 中的 YAGNI 和 Conventional Commits 约定，范围见 [MVP 文档](docs/shuiyuan-mcp-mvp.md)。

## 已知限制

- 自动测试没有使用真实水源账号；线上访问能力取决于站点版本、权限、限流和网络条件。
- 只提供 stdio 和读取能力，不实现写操作、Chat、admin API、批量爬取、Web UI 或部署编排。
- 不自动获取或刷新 User API Key；Cookie 失效时需要用户重新提供。
- 搜索摘要是上游摘要，可能已被上游截断；完整内容请用 `read_post`。主题读取期间若帖子被删除或可见性变化，可能返回响应不一致错误，重新读取即可。
- cooked HTML 的纯文本回退不保留完整排版；没有 raw 的单帖响应会报告异常，不把 HTML 冒充 Markdown。
- 插件元数据和资源的固定上限不支持继续翻页；输出会标明截断。

## 接口依据

- [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [Discourse User API Key 规范](https://meta.discourse.org/t/user-api-keys-specification/48536)
- [Discourse 搜索控制器](https://github.com/discourse/discourse/blob/main/app/controllers/search_controller.rb)
- [Discourse 主题控制器](https://github.com/discourse/discourse/blob/main/app/controllers/topics_controller.rb)
- [Discourse 用户活动控制器](https://github.com/discourse/discourse/blob/main/app/controllers/user_actions_controller.rb)
- [Discourse 投票序列化](https://github.com/discourse/discourse/blob/main/plugins/poll/app/serializers/poll_serializer.rb)
- [Retort 插件](https://github.com/gdpelican/retort)
