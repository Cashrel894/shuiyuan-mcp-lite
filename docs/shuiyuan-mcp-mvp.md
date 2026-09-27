> 后续需求更新：水源读取需要登录权限；现支持用户主动粘贴或导入 Cookie 登录文件，并在线验证。原 MVP 的匿名访问和仅环境变量凭据要求已由该需求扩展；不自动读取浏览器 Cookie、不自动登录 jAccount，MCP 保持只读。

请在当前仓库中实现一个面向上海交通大学水源社区（https://shuiyuan.sjtu.edu.cn）的标准 MCP Server。

目标是做一个“小、清晰、稳定、默认只读、可直接接入 AstrBot / Codex / Claude 等 MCP Host”的 MVP。

技术栈

使用：

- Python 3.12+
- uv
- 官方 MCP Python SDK
- httpx
- pytest

使用异步 HTTP。

不要引入不必要的框架和复杂抽象。

建议结构：

src/
shuiyuan_mcp/
**init**.py
server.py
client.py
models.py
config.py
tests/
pyproject.toml
README.md

水源 API

水源基于 Discourse。

优先调用 Discourse JSON API，不要通过 HTML 爬虫实现已有 API 能力。

Base URL：

https://shuiyuan.sjtu.edu.cn

MCP Tools

实现以下工具。

search

搜索水源帖子/主题。

使用：

GET /search.json?q=...

支持：

- keyword
- username
- category
- before
- after
- order
- status
- page

将参数转换成 Discourse 搜索语法。

返回精简后的：

- topic_id
- post_id
- title
- username
- created_at
- excerpt

不要返回整个原始 JSON。

read_topic

根据 "topic_id" 获取主题和回复。

使用：

GET /t/{topic_id}.json

支持合理的：

- offset
- limit
- max_chars

返回：

id
title
category_id
tags
posts[]

每条 post 保留：

id
post_number
username
created_at
content
reply_to_post_number

避免超长结果导致大量 token 消耗。

read_post

根据 "post_id" 获取单条帖子。

使用：

GET /posts/{post_id}.json?include_raw=true

返回：

id
topic_id
post_number
username
created_at
raw
reply_to_post_number
retorts
polls

如果 "retorts"、"polls"、"polls_votes" 不存在，应正常返回，不报错。

retorts 精简成：

{
"emoji": "...",
"usernames": ["..."]
}

polls 精简成：

{
"title": "...",
"options": [
{
"text": "...",
"votes": 10
}
]
}

get_user

根据 username 获取公开用户资料。

使用：

GET /u/{username}.json

不要返回 email 等敏感字段。

list_user_posts

获取用户最近的帖子/回复。

使用：

GET /user_actions.json?username=...&filter=4,5

支持分页/limit。

MCP Resources

实现：

shuiyuan://categories
shuiyuan://tags

分别基于：

GET /site.json
GET /tags.json

Resource 保持只读。

如果 MCP SDK 对 Resource 的实现明显增加复杂度，可以在 Tools 全部稳定后再实现。

HTTP Client

单独实现 Shuiyuan HTTP Client。

要求：

- async httpx
- timeout
- 合理 User-Agent
- 有限连接数/并发
- 不无限 retry
- 对 HTTP 429 返回清晰的 rate-limit 错误
- 5xx、timeout、connection error 做统一错误映射

不要实现批量爬虫。

鉴权

默认允许无认证访问能够公开访问的内容。

预留 Discourse User API Key 支持。

通过环境变量读取：

SHUIYUAN_BASE_URL=https://shuiyuan.sjtu.edu.cn
SHUIYUAN_USER_API_KEY=
SHUIYUAN_USER_API_CLIENT_ID=

如果配置 User API Key，则按照 Discourse User API Key 机制发送认证信息。

禁止：

- 硬编码 Token
- 保存 jAccount 密码
- 自动登录 jAccount
- 自动读取浏览器 Cookie
- 在日志中打印完整 Token/Cookie

MVP 不需要实现 User API Key 的浏览器授权流程，只需要支持用户提供已经获取的 key。

安全约束

默认只读。

本次不要实现：

- 发帖
- 回复
- 编辑
- 删除
- 上传文件
- Chat
- admin API
- Data Explorer
- 用户管理
- 全站抓取
- Web UI
- Docker/Kubernetes 部署逻辑
- 多 Discourse 站点支持

MCP 返回值设计

这是给 LLM 使用的 MCP，而不是普通 REST SDK。

必须对 Discourse JSON 做 normalization。

不要把巨大原始 JSON 原样返回。

优先返回：

- 对模型真正有用的字段
- Markdown/raw 内容
- ID
- URL
- 时间
- 用户名
- 必要元数据

对于可能很长的内容提供：

limit
offset
max_chars

并设置合理默认值。

错误处理

至少区分：

- Not Found
- Authentication Required
- Permission Denied
- Rate Limited
- Shuiyuan Unreachable
- Timeout
- Unexpected API Response

不要把完整 Python traceback 返回给 MCP Client。

所有日志写入 stderr。

绝对不要向 stdout 打日志，因为 stdio transport 的 stdout 专用于 MCP protocol。

CLI

这是重要交付要求。

在 "pyproject.toml" 中提供 CLI entry point，使项目安装后能够直接执行：

uv run shuiyuan-mcp

该命令：

- 启动 stdio MCP Server
- 不要求交互式终端
- 可以由 AstrBot fork/exec
- 配置全部通过环境变量获得
- stdout 仅用于 MCP protocol
- stderr 用于日志

测试

至少覆盖：

- search query 构造
- read_post normalization
- read_topic normalization
- retorts/polls 缺失情况
- HTTP 404
- HTTP 401/403
- HTTP 429
- timeout
- response schema 异常

HTTP 测试全部使用 mock。

自动测试不要请求真实水源。

README

README 必须包含：

安装

uv sync

启动

uv run shuiyuan-mcp

环境变量

说明：

SHUIYUAN_BASE_URL
SHUIYUAN_USER_API_KEY
SHUIYUAN_USER_API_CLIENT_ID

MCP 能力

列出所有 Tools 和 Resources。

AstrBot

给出：

{
"command": "uv",
"args": [
"--directory",
"/AstrBot/data/mcp/shuiyuan-mcp",
"run",
"shuiyuan-mcp"
]
}

不要加入任何 AstrBot 专用 Python 依赖。

项目必须仍然是标准 MCP Server。

开发流程

开始前：

1. 阅读当前仓库。
2. 阅读 AGENTS.md / CONTRIBUTING.md（如果存在）。
3. 检查已有实现和依赖。
4. 给出一个简短计划。
5. 直接开始实现，不等待确认。

开发过程中：

- 小步修改
- 不做 MVP 外重构
- 不增加无必要依赖
- 每完成一部分就运行相关测试
- API 行为不确定时查 Discourse 官方资料或已有开源实现，不要猜

可参考：

- dajiaohuang/shuiyuan-mcp
- Okabe-Rintarou-0/Shuiyuan-Client
- Discourse 官方 API 文档
- MCP 官方 Python SDK

参考实现只用于确认接口和数据结构，不要无脑复制。

最终验收

完成后必须运行：

- formatter
- linter（如果项目配置）
- 全部 tests

修复所有由本次修改导致的错误。

最后给出简短交付报告：

1. 实现了哪些 MCP Tools / Resources
2. 关键文件
3. 测试结果
4. "uv run shuiyuan-mcp" 是否正常启动
5. AstrBot 配置 JSON
6. 已知限制

最终产物必须能够直接通过：

uv sync
uv run shuiyuan-mcp

启动，并能够作为 AstrBot 的 stdio MCP Server 使用。
