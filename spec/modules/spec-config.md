[ ] # 模块规范 — 配置系统 (config)

---

[ ] ## 1. 模块概述

[ ] ### 1.1 职责
[ ] 提供全局配置管理，支持 TOML 配置文件解析、pydantic 模型验证、环境变量回退。

[ ] ### 1.2 文件位置
[ ] - `app/config.py` — 配置模型定义与加载器
[ ] - `config/config.toml` — 活跃配置文件
[ ] - `config/config.example-*.toml` — 示例配置文件

[ ] ## 2. 配置模型

[ ] | 选择 | 模型类 | 说明 |
[ ] |:---:|--------|------|
[ ] | [ ] | `LLMSettings` | LLM 连接配置（模型、API 密钥、base_url、temperature 等） |
[ ] | [ ] | `BrowserSettings` | 浏览器自动化配置（headless、代理、Chromium 参数等） |
[ ] | [ ] | `SearchSettings` | 搜索引擎配置（引擎选择、回退链、语言/国家） |
[ ] | [ ] | `SandboxSettings` | Docker 沙箱配置（镜像、资源限制、超时） |
[ ] | [ ] | `DaytonaSettings` | Daytona 云沙箱配置（API 密钥、服务器 URL、沙箱镜像等） |
[ ] | [ ] | `MCPSettings` | MCP 协议配置（服务器引用、服务器列表） |
[ ] | [ ] | `RunflowSettings` | 多 Agent 流程配置 |

[ ] ## 3. 配置加载流程

[ ] ```
[ ] Config() 单例
[ ]   └── _load_initial_config()
[ ]         ├── _get_config_path() → config/config.toml
[ ]         ├── tomllib.load() → raw_config dict
[ ]         ├── 解析 [llm] 段 → LLMSettings（支持 env var 回退）
[ ]         ├── 解析 [browser] 段 → BrowserSettings
[ ]         ├── 解析 [search] 段 → SearchSettings
[ ]         ├── 解析 [sandbox] 段 → SandboxSettings
[ ]         ├── 解析 [daytona] 段 → DaytonaSettings（支持 DAYTONA_API_KEY env var 回退）
[ ]         ├── 解析 [mcp] 段 → MCPSettings（从 mcp.json 加载服务器列表）
[ ]         └── 解析 [runflow] 段 → RunflowSettings
[ ] ```

[ ] ## 4. 环境变量支持

[ ] | 选择 | 配置项 | 环境变量 | 说明 |
[ ] |:---:|--------|----------|------|
[ ] | [ ] | llm.default.api_key | `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` | LLM API 密钥 |
[ ] | [ ] | daytona.daytona_api_key | `DAYTONA_API_KEY` | Daytona API 密钥 |

[ ] ## 5. 更新历史

[ ] | 选择 | 版本 | 更新日期 | 更新内容 |
[ ] |:---:|------|----------|----------|
[ ] | [ ] | v1.0 | 2026-07-10 | 初始版本，定义配置系统模块 |
