import json
import traceback
from typing import Optional

from pydantic import Field

from app.daytona.tool_base import (
    Sandbox,
    SandboxToolsBase,
    ThreadMessage,
)
from app.logger import logger
from app.tool.base import ToolResult

_BROWSER_DESCRIPTION = """\
基于沙箱的浏览器自动化工具，允许通过各种操作与网页交互。
* 此工具提供在沙箱环境中控制浏览器会话的命令
* 它在调用之间维护状态，保持浏览器会话活动直到显式关闭
* 当您需要在安全沙箱中浏览网站、填写表单、点击按钮或提取内容时使用此工具
主要功能包括：
* 导航：访问特定 URL，返回历史记录
* 交互：按索引点击元素、输入文本、发送键盘命令
* 滚动：按像素量向上/向下滚动或滚动到特定文本
* 标签页管理：在标签页之间切换或关闭标签页
* 内容提取：获取下拉选项或选择下拉选项
"""


class SandboxBrowserTool(SandboxToolsBase):
    """用于在 Daytona 沙箱中执行任务的工具，具有浏览器使用功能。"""

    name: str = "sandbox_browser"
    description: str = _BROWSER_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "navigate_to",
                    "go_back",
                    "wait",
                    "click_element",
                    "input_text",
                    "send_keys",
                    "switch_tab",
                    "close_tab",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "click_coordinates",
                    "drag_drop",
                ],
                "description": "要执行的浏览器操作",
            },
            "url": {"type": "string", "description": "'navigate_to' 操作的 URL"},
            "index": {"type": "integer", "description": "交互操作的元素索引"},
            "text": {"type": "string", "description": "输入或滚动操作的文本"},
            "amount": {"type": "integer", "description": "滚动像素量"},
            "page_id": {"type": "integer", "description": "标签页管理操作的标签页 ID"},
            "keys": {"type": "string", "description": "键盘操作要发送的按键"},
            "seconds": {"type": "integer", "description": "等待秒数"},
            "x": {"type": "integer", "description": "点击或拖拽操作的 X 坐标"},
            "y": {"type": "integer", "description": "点击或拖拽操作的 Y 坐标"},
            "element_source": {"type": "string", "description": "拖放的源元素"},
            "element_target": {"type": "string", "description": "拖放的目标元素"},
        },
        "required": ["action"],
        "dependencies": {
            "navigate_to": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "send_keys": ["keys"],
            "switch_tab": ["page_id"],
            "close_tab": ["page_id"],
            "scroll_down": ["amount"],
            "scroll_up": ["amount"],
            "scroll_to_text": ["text"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "click_coordinates": ["x", "y"],
            "drag_drop": ["element_source", "element_target"],
            "wait": ["seconds"],
        },
    }
    browser_message: Optional[ThreadMessage] = Field(default=None, exclude=True)

    def __init__(
        self, sandbox: Optional[Sandbox] = None, **data
    ):
        """使用可选的 sandbox 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    async def _check_browser_service_health(self) -> tuple:
        """检查浏览器自动化服务是否可用"""
        try:
            await self._ensure_sandbox()
            check_cmd = "curl -s -f --max-time 5 http://localhost:8003/health 2>&1"
            response = self.sandbox.process.exec(check_cmd, timeout=10)
            if response.exit_code == 0:
                return True, "Service healthy"

            # 检查端口是否监听
            port_check = ("netstat -tlnp 2>/dev/null | grep ':8003' "
                          "|| ss -tlnp 2>/dev/null | grep ':8003' "
                          "|| echo 'PORT_NOT_LISTENING'")
            port_response = self.sandbox.process.exec(port_check, timeout=5)
            if "PORT_NOT_LISTENING" in port_response.result or port_response.exit_code != 0:
                return False, "Browser automation service not started (port 8003 not listening)"

            return False, "Browser automation service starting up, please retry later"
        except Exception as e:
            return False, f"Health check failed: {str(e)}"

    async def _execute_browser_action(
        self, endpoint: str, params: dict = None, method: str = "POST"
    ) -> ToolResult:
        """通过沙箱 API 执行浏览器自动化操作。"""
        try:
            await self._ensure_sandbox()

            # 检查服务健康状态
            is_healthy, health_msg = await self._check_browser_service_health()
            if not is_healthy:
                vnc_url = ""
                try:
                    if hasattr(self, '_sandbox') and self._sandbox:
                        vnc_link = self._sandbox.get_preview_link(6080)
                        vnc_url = vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
                except:
                    pass

                return self.fail_response(
                    f"Browser automation service unavailable: {health_msg}\n"
                    f"VNC URL: {vnc_url if vnc_url else 'See logs'}"
                )

            url = f"http://localhost:8003/api/automation/{endpoint}"
            if method == "GET" and params:
                query_params = "&".join([f"{k}={v}" for k, v in params.items()])
                url = f"{url}?{query_params}"
                curl_cmd = f"curl -s -X {method} '{url}' -H 'Content-Type: application/json'"
            else:
                curl_cmd = f"curl -s -X {method} '{url}' -H 'Content-Type: application/json'"
                if params:
                    json_data = json.dumps(params)
                    curl_cmd += f" -d '{json_data}'"

            logger.debug(f"Executing curl command: {curl_cmd}")
            response = self.sandbox.process.exec(curl_cmd, timeout=30)

            if response.exit_code == 0:
                try:
                    result = json.loads(response.result)
                    result.setdefault("content", "")
                    result.setdefault("role", "assistant")

                    message = ThreadMessage(
                        type="browser_state", content=result, is_llm_message=False
                    )
                    self.browser_message = message

                    success_response = {
                        "success": result.get("success", False),
                        "message": result.get("message", "Browser action completed"),
                    }
                    for field in ["url", "title", "element_count",
                                  "pixels_below", "ocr_text", "image_url"]:
                        if field in result:
                            success_response[field] = result[field]

                    return (
                        self.success_response(success_response)
                        if success_response["success"]
                        else self.fail_response(str(success_response))
                    )
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse response JSON: {e}")
                    return self.fail_response(f"Failed to parse response JSON: {e}")
            else:
                vnc_url = ""
                try:
                    if hasattr(self, '_sandbox') and self._sandbox:
                        vnc_link = self._sandbox.get_preview_link(6080)
                        vnc_url = vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
                except:
                    pass

                return self.fail_response(
                    f"Browser automation request failed (exit_code={response.exit_code})\n"
                    f"VNC URL: {vnc_url if vnc_url else 'See logs'}"
                )
        except Exception as e:
            logger.error(f"Error executing browser action: {e}")
            logger.debug(traceback.format_exc())
            return self.fail_response(f"Error executing browser action: {e}")

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        amount: Optional[int] = None,
        page_id: Optional[int] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        element_source: Optional[str] = None,
        element_target: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        """在沙箱环境中执行浏览器操作。"""
        try:
            if action == "navigate_to":
                if not url:
                    return self.fail_response("URL is required for navigation")
                return await self._execute_browser_action("navigate_to", {"url": url})
            elif action == "go_back":
                return await self._execute_browser_action("go_back", {})
            elif action == "click_element":
                if index is None:
                    return self.fail_response("Index is required for click_element")
                return await self._execute_browser_action("click_element", {"index": index})
            elif action == "input_text":
                if index is None or not text:
                    return self.fail_response("Index and text are required for input_text")
                return await self._execute_browser_action("input_text", {"index": index, "text": text})
            elif action == "send_keys":
                if not keys:
                    return self.fail_response("Keys are required for send_keys")
                return await self._execute_browser_action("send_keys", {"keys": keys})
            elif action == "switch_tab":
                if page_id is None:
                    return self.fail_response("Page ID is required for switch_tab")
                return await self._execute_browser_action("switch_tab", {"page_id": page_id})
            elif action == "close_tab":
                if page_id is None:
                    return self.fail_response("Page ID is required for close_tab")
                return await self._execute_browser_action("close_tab", {"page_id": page_id})
            elif action == "scroll_down":
                params = {"amount": amount} if amount is not None else {}
                return await self._execute_browser_action("scroll_down", params)
            elif action == "scroll_up":
                params = {"amount": amount} if amount is not None else {}
                return await self._execute_browser_action("scroll_up", params)
            elif action == "scroll_to_text":
                if not text:
                    return self.fail_response("Text is required for scroll_to_text")
                return await self._execute_browser_action("scroll_to_text", {"text": text})
            elif action == "get_dropdown_options":
                if index is None:
                    return self.fail_response("Index is required for get_dropdown_options")
                return await self._execute_browser_action("get_dropdown_options", {"index": index})
            elif action == "select_dropdown_option":
                if index is None or not text:
                    return self.fail_response("Index and text are required for select_dropdown_option")
                return await self._execute_browser_action("select_dropdown_option", {"index": index, "text": text})
            elif action == "click_coordinates":
                if x is None or y is None:
                    return self.fail_response("X and Y coordinates are required for click_coordinates")
                return await self._execute_browser_action("click_coordinates", {"x": x, "y": y})
            elif action == "drag_drop":
                if not element_source or not element_target:
                    return self.fail_response("Source and target elements are required for drag_drop")
                return await self._execute_browser_action(
                    "drag_drop",
                    {"element_source": element_source, "element_target": element_target},
                )
            elif action == "wait":
                seconds_to_wait = seconds if seconds is not None else 3
                return await self._execute_browser_action("wait", {"seconds": seconds_to_wait})
            else:
                return self.fail_response(f"Unknown action: {action}")
        except Exception as e:
            logger.error(f"Error executing browser action: {e}")
            return self.fail_response(f"Error executing browser action: {e}")

    @classmethod
    def create_with_sandbox(cls, sandbox: Sandbox) -> "SandboxBrowserTool":
        """创建带有沙箱的工具的工厂方法。"""
        return cls(sandbox=sandbox)
