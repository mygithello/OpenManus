[ ] # 模块规范 — Daytona 云沙箱 (daytona)

---

[ ] ## 1. 模块概述

[ ] ### 1.1 职责
[ ] 提供基于 Daytona 云服务的远程沙箱执行环境。封装 daytona-sdk，负责沙箱的创建、启动、停止、删除等生命周期管理，以及为沙箱工具提供统一的基类支持。通过 `config.toml` 或环境变量 `DAYTONA_API_KEY` 配置。

[ ] ### 1.2 依赖关系
[ ] | 选择 | 依赖模块 | 说明 |
[ ] |:---:|----------|------|
[ ] | [ ] | config | 读取 `[daytona]` 配置段，获取 API 密钥等参数 |
[ ] | [ ] | tool | 继承 `BaseTool` 基类，提供 `success_response`/`fail_response` 方法 |

[ ] ### 1.3 外部依赖
[ ] - `daytona-sdk>=0.2.0` — Daytona Python SDK

[ ] ## 2. 核心组件

[ ] ### 2.1 配置层 `app/config.py` → `DaytonaSettings`
[ ] | 选择 | 字段 | 类型 | 说明 | 默认值 |
[ ] |:---:|------|------|------|--------|
[ ] | [ ] | daytona_api_key | Optional[str] | Daytona API 密钥，优先从环境变量 `DAYTONA_API_KEY` 读取 | None |
[ ] | [ ] | daytona_server_url | Optional[str] | Daytona 服务器 URL | `https://app.daytona.io/api` |
[ ] | [ ] | daytona_target | Optional[str] | 区域选择：`eu` 或 `us` | `us` |
[ ] | [ ] | sandbox_image_name | Optional[str] | 沙箱 Docker 镜像 | `whitezxj/sandbox:0.1.0` |
[ ] | [ ] | sandbox_entrypoint | Optional[str] | 沙箱入口点命令 | `/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf` |
[ ] | [ ] | VNC_password | Optional[str] | VNC 密码 | `123456` |

[ ] ### 2.2 沙箱生命周期 `app/daytona/sandbox.py`
[ ] | 选择 | 函数 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | `create_sandbox(password)` | 创建新沙箱，启动 supervisord，等待服务就绪 |
[ ] | [ ] | `get_or_start_sandbox(sandbox_id)` | 根据 ID 检索沙箱，按需恢复归档/停止状态的沙箱 |
[ ] | [ ] | `delete_sandbox(sandbox_id)` | 根据 ID 删除沙箱 |
[ ] | [ ] | `start_supervisord_session(sandbox)` | 在沙箱会话中启动 supervisord |

[ ] ### 2.3 沙箱工具基类 `app/daytona/tool_base.py`
[ ] | 选择 | 类/函数 | 说明 |
[ ] |:---:|---------|------|
[ ] | [ ] | `SandboxToolsBase` | 所有沙箱工具的基类，提供沙箱实例管理和路径清理 |
[ ] | [ ] | `ThreadMessage` | 线程消息类型，用于浏览器状态和图片上下文的传递 |
[ ] | [ ] | `_ensure_sandbox()` | 确保沙箱存在，按需创建或恢复 |

[ ] ## 3. 资源规格

[ ] | 选择 | 资源 | 规格 | 说明 |
[ ] |:---:|------|------|------|
[ ] | [ ] | CPU | 2 vCPU | 沙箱的 CPU 资源 |
[ ] | [ ] | 内存 | 4 GB | 沙箱的内存资源 |
[ ] | [ ] | 磁盘 | 5 GB | 沙箱的磁盘资源 |
[ ] | [ ] | 自动停止 | 15 分钟 | 空闲自动停止间隔 |
[ ] | [ ] | 自动归档 | 1440 分钟 | 停止后自动归档间隔 |
[ ] | [ ] | 公开访问 | 是 | 沙箱具有公开可访问的预览链接 |

[ ] ## 4. 沙箱工具

[ ] ### 4.1 SandboxFilesTool `app/tool/sandbox/sb_files_tool.py`
[ ] | 选择 | 操作 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | view | 查看文件内容，支持行范围 |
[ ] | [ ] | create_file | 创建新文件 |
[ ] | [ ] | str_replace | 替换文件中特定字符串 |
[ ] | [ ] | full_file_rewrite | 完全重写文件 |
[ ] | [ ] | delete_file | 删除文件 |

[ ] ### 4.2 SandboxShellTool `app/tool/sandbox/sb_shell_tool.py`
[ ] | 选择 | 操作 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | execute_command | 在 tmux 会话中执行命令 |
[ ] | [ ] | check_command_output | 检查 tmux 会话输出 |
[ ] | [ ] | terminate_command | 终止 tmux 会话 |
[ ] | [ ] | list_commands | 列出活跃的 tmux 会话 |

[ ] ### 4.3 SandboxBrowserTool `app/tool/sandbox/sb_browser_tool.py`
[ ] | 选择 | 操作 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | navigate_to | 导航到 URL |
[ ] | [ ] | go_back | 返回历史记录 |
[ ] | [ ] | click_element | 按索引点击元素 |
[ ] | [ ] | input_text | 输入文本 |
[ ] | [ ] | send_keys | 发送键盘命令 |
[ ] | [ ] | switch_tab/close_tab | 标签页管理 |
[ ] | [ ] | scroll_down/scroll_up/scroll_to_text | 滚动操作 |
[ ] | [ ] | click_coordinates | 按坐标点击 |
[ ] | [ ] | drag_drop | 拖放操作 |

[ ] ### 4.4 SandboxVisionTool `app/tool/sandbox/sb_vision_tool.py`
[ ] | 选择 | 操作 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | see_image | 读取并压缩图片，返回 base64 |

[ ] ## 5. Agent

[ ] ### 5.1 SandboxManus `app/agent/sandbox_agent.py`
[ ] | 选择 | 特性 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | 沙箱工具集合 | 初始化时自动创建沙箱并附加 sandbox 工具 |
[ ] | [ ] | MCP 服务器连接 | 支持 SSE/stdio MCP 服务器接入 |
[ ] | [ ] | 沙箱清理 | Agent 退出时自动删除沙箱 |

[ ] ### 5.2 入口点 `sandbox_main.py`
[ ] | 选择 | 选项 | 说明 |
[ ] |:---:|------|------|
[ ] | [ ] | `--prompt TEXT` | 直接提供提示词运行 |
[ ] | [ ] | 交互模式 | 无参数时等待用户输入 |

[ ] ## 6. 更新历史

[ ] | 选择 | 版本 | 更新日期 | 更新内容 |
[ ] |:---:|------|----------|----------|
[ ] | [ ] | v1.0 | 2026-07-10 | 初始版本，定义 Daytona 云沙箱模块 |
