# 仓库开发约定

## 项目范围

- 本项目是面向上海交通大学水源社区的轻量级标准 MCP Server，默认只读，通过 stdio 接入 AstrBot / Codex / Claude 等 MCP Host。
- MVP 需求见 `docs/shuiyuan-mcp-mvp.md`。实现相关功能前阅读该文档，并检查当前代码和依赖；不要将规划中的能力视为已实现。
- 计划技术栈为 Python 3.12+、uv、官方 MCP Python SDK、httpx 和 pytest。

## 开发原则

- 严格遵循 **YAGNI（You Aren't Gonna Need It）**：只实现当前明确需要的功能，不为假设的未来需求增加接口、配置、抽象或依赖。
- 优先采用简单、直接、清晰的实现；仅在现有需求确实需要时提取共用逻辑。
- 小步修改，保持变更聚焦；不做任务范围外的重构，不提前实现 MVP 排除的功能。
- 开始前阅读适用的 `AGENTS.md`、`CONTRIBUTING.md`（如有）、相关实现与依赖，给出简短计划后开始工作。
- API 行为不确定时查阅官方资料或已有开源实现，不凭空猜测。

## 实现与安全

- 优先使用 Discourse JSON API；HTTP 请求使用异步 httpx，设置超时和合理并发限制，不无限重试。
- MCP 返回值应精简和规范化，对长内容及列表设置合理边界，避免直接返回巨大原始 JSON。
- 保持默认只读。密钥通过环境变量读取，不硬编码、不输出完整 Token/Cookie，不自动登录 jAccount 或读取浏览器 Cookie。
- stdio 的 stdout 仅用于 MCP 协议，日志写入 stderr；不向 MCP Client 返回完整 Python traceback。

## 验证

- 为功能变更和缺陷修复运行相关测试；HTTP 自动测试使用 mock，不请求真实水源。
- 功能交付前运行项目配置的 formatter、linter（如有）和全部测试，修复本次变更引起的问题。
- 预期安装和启动命令为 `uv sync`、`uv run shuiyuan-mcp`；测试使用 `uv run pytest`。仅在相应配置存在后执行，不宣称尚未执行的检查已通过。
- 仅修改文档时检查内容及格式，无需添加无关测试。

## 提交规范

- 遵循 **Conventional Commits**：`<type>[optional scope]: <description>`。
- 常用 type：`feat`、`fix`、`docs`、`refactor`、`test`、`build`、`ci`、`chore`、`perf`、`style`。
- description 简洁、准确地说明本次变更；单次提交保持一个明确目的。
- 破坏性变更使用 `!` 标识或在提交正文的 footer 中添加 `BREAKING CHANGE: ...`。
- 示例：`feat(search): 支持按用户名筛选`、`fix(client): 正确映射限流错误`、`docs: 补充安装说明`。
