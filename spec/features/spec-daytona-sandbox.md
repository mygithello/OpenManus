[ ] # 功能点规范 — Daytona 沙箱

---

[ ] ## 1. 功能概述

[ ] ### 1.1 功能描述
[ ] 集成 Daytona 云沙箱服务，为 AI Agent 提供远程、安全的代码执行环境。支持沙箱生命周期管理、文件系统操作、Shell 命令执行、浏览器自动化和视觉图片读取。

[ ] ### 1.2 所属模块
[ ] - [ ] daytona (app/daytona/)
[ ] - [ ] tool/sandbox (app/tool/sandbox/)

[ ] ## 2. 配置

[ ] | 选择 | 配置项 | 类型 | 说明 | 默认值 |
[ ] |:---:|--------|------|------|--------|
[ ] | [ ] | daytona_api_key | string | API 密钥，留空则从 `DAYTONA_API_KEY` 环境变量读取 | `""` |
[ ] | [ ] | daytona_server_url | string | 服务器 URL | `https://app.daytona.io/api` |
[ ] | [ ] | daytona_target | string | 区域 | `us` |
[ ] | [ ] | sandbox_image_name | string | 沙箱镜像 | `whitezxj/sandbox:0.1.0` |
[ ] | [ ] | VNC_password | string | VNC 密码 | `123456` |

[ ] ## 3. 数据流

[ ] ```
[ ] sandbox_main.py
[ ]   └── SandboxManus.create()
[ ]         ├── initialize_sandbox_tools()
[ ]         │     └── create_sandbox(password)
[ ]         │           ├── config.daytona → DaytonaSettings
[ ]         │           ├── Daytona(config) → daytona_sdk
[ ]         │           ├── daytona.create(params) → Sandbox
[ ]         │           └── start_supervisord_session(sandbox)
[ ]         └── 工具附加
[ ]               ├── SandboxFilesTool(sandbox)
[ ]               ├── SandboxShellTool(sandbox)
[ ]               ├── SandboxBrowserTool(sandbox)
[ ]               └── SandboxVisionTool(sandbox)
[ ] ```

[ ] ## 4. 验证标准

[ ] | 选择 | 验证项 | 验证方法 | 期望结果 |
[ ] |:---:|--------|----------|----------|
[ ] | [ ] | 配置加载 | 从 config.toml 读取 Daytona 配置 | 正确解析全部字段 |
[ ] | [ ] | 环境变量回退 | 清空 config 中 api_key，设置 `DAYTONA_API_KEY` 环境变量 | 正确读取环境变量 |
[ ] | [ ] | 模块导入 | Python 导入 `app.daytona` 包下全部模块 | 无 import 错误 |
[ ] | [ ] | sandbox_tools 编译 | 导入所有沙箱工具类 | 无语法/类型错误 |

[ ] ## 5. 更新历史

[ ] | 选择 | 版本 | 更新日期 | 更新内容 |
[ ] |:---:|------|----------|----------|
[ ] | [ ] | v1.0 | 2026-07-10 | 初始版本，定义 Daytona 沙箱功能点 |
