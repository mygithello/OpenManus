import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, Optional

from daytona_sdk import Daytona, DaytonaConfig, Sandbox, SandboxState
from pydantic import ConfigDict, Field, PrivateAttr

from app.config import config
from app.daytona.sandbox import create_sandbox, start_supervisord_session, daytona
from app.logger import logger
from app.tool.base import BaseTool


@dataclass
class ThreadMessage:
    """
    表示要添加到线程的消息。
    """

    type: str
    content: Dict[str, Any]
    is_llm_message: bool = False
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[float] = field(
        default_factory=lambda: datetime.now().timestamp()
    )

    def to_dict(self) -> Dict[str, Any]:
        """将消息转换为字典以供 API 调用"""
        return {
            "type": self.type,
            "content": self.content,
            "is_llm_message": self.is_llm_message,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }


def _clean_workspace_path(path: str, workspace_path: str = "/workspace") -> str:
    """清理并规范化路径，使其相对于工作区。

    Args:
        path: 要清理的路径
        workspace_path: 要移除的基本工作区路径（默认: "/workspace"）

    Returns:
        清理后的路径，相对于工作区
    """
    # 移除开头的斜杠
    path = path.lstrip("/")

    # 如果存在 workspace 前缀则移除
    if path.startswith(workspace_path.lstrip("/")):
        path = path[len(workspace_path.lstrip("/")):]

    # 移除 workspace/ 前缀（如果存在）
    if path.startswith("workspace/"):
        path = path[9:]

    # 移除剩余的开头斜杠
    path = path.lstrip("/")

    return path


class SandboxToolsBase(BaseTool):
    """所有沙箱工具的基类，提供基于项目的沙箱访问。"""

    # 类变量，用于跟踪是否已打印沙箱 URL
    _urls_printed: ClassVar[bool] = False

    # 必需字段
    project_id: Optional[str] = None

    # 私有字段（pydantic v2 使用 PrivateAttr，不属于模型模式）
    _sandbox: Optional[Sandbox] = PrivateAttr(default=None)
    _sandbox_id: Optional[str] = PrivateAttr(default=None)
    _sandbox_pass: Optional[str] = PrivateAttr(default=None)
    workspace_path: str = Field(default="/workspace", exclude=True)
    _sessions: dict[str, str] = PrivateAttr(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def _ensure_sandbox(self) -> Sandbox:
        """确保我们有一个有效的沙箱实例，如果需要则创建它。"""
        if self._sandbox is None:
            if config.daytona is None:
                raise RuntimeError(
                    "Daytona configuration not found. Please configure daytona_api_key in config.toml "
                    "or set DAYTONA_API_KEY environment variable."
                )
            try:
                password = config.daytona.VNC_password or "123456"
                self._sandbox = create_sandbox(password=password)
                # 如果尚未打印，则记录 URL
                if not SandboxToolsBase._urls_printed:
                    vnc_link = self._sandbox.get_preview_link(6080)
                    website_link = self._sandbox.get_preview_link(8080)

                    vnc_url = (
                        vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
                    )
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link)
                    )

                    print("\033[95m***")
                    print(f"VNC URL: {vnc_url}")
                    print(f"Website URL: {website_url}")
                    print("***\033[0m")
                    SandboxToolsBase._urls_printed = True
            except Exception as e:
                logger.error(f"Error retrieving or starting sandbox: {str(e)}")
                raise e
        else:
            if (
                self._sandbox.state == SandboxState.ARCHIVED
                or self._sandbox.state == SandboxState.STOPPED
            ):
                logger.info(f"Sandbox is in {self._sandbox.state} state. Starting...")
                try:
                    if daytona is None:
                        raise RuntimeError("Daytona client is not initialized")
                    daytona.start(self._sandbox)

                    # 重启时在会话中启动 supervisord
                    start_supervisord_session(self._sandbox)
                except Exception as e:
                    logger.error(f"Error starting sandbox: {e}")
                    raise e
        return self._sandbox

    @property
    def sandbox(self) -> Sandbox:
        """获取沙箱实例，确保它存在。"""
        if self._sandbox is None:
            raise RuntimeError("Sandbox not initialized. Call _ensure_sandbox() first.")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        """获取沙箱 ID，确保它存在。"""
        if self._sandbox_id is None:
            raise RuntimeError(
                "Sandbox ID not initialized. Call _ensure_sandbox() first."
            )
        return self._sandbox_id

    def clean_path(self, path: str) -> str:
        """清理并规范化路径，使其相对于 /workspace。"""
        cleaned_path = _clean_workspace_path(path, self.workspace_path)
        logger.debug(f"Cleaned path: {path} -> {cleaned_path}")
        return cleaned_path
