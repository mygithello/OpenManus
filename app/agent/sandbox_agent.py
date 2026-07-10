from typing import Dict, List, Optional

from pydantic import Field, model_validator

from app.agent.browser import BrowserContextHelper
from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.daytona.sandbox import create_sandbox, delete_sandbox
from app.daytona.tool_base import SandboxToolsBase
from app.logger import logger
from app.prompt.manus import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool import Terminate, ToolCollection
from app.tool.mcp import MCPClients
from app.tool.sandbox.sb_browser_tool import SandboxBrowserTool
from app.tool.sandbox.sb_files_tool import SandboxFilesTool
from app.tool.sandbox.sb_shell_tool import SandboxShellTool
from app.tool.sandbox.sb_vision_tool import SandboxVisionTool


class SandboxManus(ToolCallAgent):
    """一个通用的多功能 agent，在 Daytona 沙箱中运行工具。"""

    name: str = "SandboxManus"
    description: str = "一个多功能的 agent，可以使用多种沙箱工具解决各种任务"

    system_prompt: str = SYSTEM_PROMPT.format(directory=config.workspace_root)
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 20

    # MCP 客户端，用于远程工具访问
    mcp_clients: MCPClients = Field(default_factory=MCPClients)

    # 初始工具集合（沙箱工具在初始化时动态添加）
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            Terminate(),
        )
    )

    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])
    browser_context_helper: Optional[BrowserContextHelper] = None

    _initialized: bool = False
    _sandbox_initialized: bool = False
    sandbox_link: Optional[dict[str, dict[str, str]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def initialize_helper(self) -> "SandboxManus":
        """同步初始化基本组件。"""
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    @classmethod
    async def create(cls, **kwargs) -> "SandboxManus":
        """工厂方法，创建并正确初始化 SandboxManus 实例。"""
        instance = cls(**kwargs)
        await instance.initialize_mcp_servers()
        await instance.initialize_sandbox_tools()
        instance._initialized = True
        return instance

    async def initialize_sandbox_tools(
        self,
        password: Optional[str] = None,
    ) -> None:
        try:
            if password is None:
                if config.daytona is None:
                    logger.warning("Daytona configuration not found. Skipping sandbox initialization.")
                    return
                password = config.daytona.VNC_password

            if password:
                sandbox = create_sandbox(password=password)
            else:
                raise ValueError("password must be provided")

            vnc_link = sandbox.get_preview_link(6080)
            website_link = sandbox.get_preview_link(8080)
            vnc_url = vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
            website_url = (
                website_link.url if hasattr(website_link, "url") else str(website_link)
            )

            actual_sandbox_id = sandbox.id if hasattr(sandbox, "id") else "new_sandbox"
            if not self.sandbox_link:
                self.sandbox_link = {}
            self.sandbox_link[actual_sandbox_id] = {
                "vnc": vnc_url,
                "website": website_url,
            }
            logger.info(f"VNC URL: {vnc_url}")
            logger.info(f"Website URL: {website_url}")
            logger.info("Daytona sandbox initialized successfully with sandbox tools")
            SandboxToolsBase._urls_printed = True

            sb_tools = [
                SandboxBrowserTool(sandbox),
                SandboxFilesTool(sandbox),
                SandboxShellTool(sandbox),
                SandboxVisionTool(sandbox),
            ]
            self.available_tools.add_tools(*sb_tools)
            self._sandbox_initialized = True

        except Exception as e:
            logger.error(f"Error initializing sandbox tools: {e}")
            raise

    async def initialize_mcp_servers(self) -> None:
        """初始化与已配置的 MCP 服务器的连接（如果配置存在）。"""
        # 获取 MCP 服务器配置（支持通过 mcp 模块自定义加载）
        servers = getattr(config.mcp_config, 'servers', None)
        if not servers:
            logger.debug("No MCP servers configured, skipping MCP initialization")
            return

        for server_id, server_config in servers.items():
            try:
                if server_config.type == "sse":
                    if server_config.url:
                        await self.mcp_clients.connect_sse(server_config.url)
                        logger.info(
                            f"Connected to MCP server '{server_id}' at {server_config.url}"
                        )
                elif server_config.type == "stdio":
                    if server_config.command:
                        await self.mcp_clients.connect_stdio(
                            server_config.command,
                            server_config.args or [],
                        )
                        logger.info(
                            f"Connected to MCP server '{server_id}' using command {server_config.command}"
                        )
                # MCP 工具连接后自动进入工具映射，将其添加到 available_tools
                if self.mcp_clients.tools:
                    self.available_tools.add_tools(*list(self.mcp_clients.tools))
            except Exception as e:
                logger.error(f"Failed to connect to MCP server '{server_id}': {e}")

    async def disconnect_mcp_server(self) -> None:
        """断开 MCP 服务器连接。"""
        try:
            await self.mcp_clients.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting MCP server: {e}")

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """根据 ID 删除沙箱。"""
        try:
            await delete_sandbox(sandbox_id)
            logger.info(f"Sandbox {sandbox_id} deleted successfully")
            if sandbox_id in self.sandbox_link:
                del self.sandbox_link[sandbox_id]
        except Exception as e:
            logger.error(f"Error deleting sandbox {sandbox_id}: {e}")

    async def cleanup(self):
        """清理 SandboxManus agent 资源。"""
        if self.browser_context_helper:
            await self.browser_context_helper.cleanup_browser()
        if self._initialized:
            await self.disconnect_mcp_server()
            # Cleanup: delete sandbox if it was created
            if self._sandbox_initialized and self.sandbox_link:
                for sid in list(self.sandbox_link.keys()):
                    await self.delete_sandbox(sid)
            self._initialized = False

    async def think(self) -> bool:
        """处理当前状态，并在适当的上下文中决定下一步行动。"""
        if not self._initialized:
            await self.initialize_mcp_servers()
            self._initialized = True

        original_prompt = self.next_step_prompt
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []
        browser_in_use = any(
            tc.function.name == SandboxBrowserTool().name
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )

        result = await super().think()

        self.next_step_prompt = original_prompt
        return result
