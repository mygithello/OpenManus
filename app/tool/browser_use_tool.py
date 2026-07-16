import asyncio
import base64
import json
import re
from datetime import datetime
from typing import Generic, Optional, TypeVar

from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from pydantic import Field, field_validator
from pydantic_core.core_schema import ValidationInfo

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.web_search import WebSearch


_BROWSER_DESCRIPTION = """\
一个强大的浏览器自动化工具，允许通过各种操作与网页交互。
* 此工具提供用于控制浏览器会话、导航网页和提取信息的命令
* 它在调用之间保持状态，保持浏览器会话活动直到显式关闭
* 当你需要浏览网站、填写表单、点击按钮、提取内容或执行网页搜索时使用此工具
* 每个操作都需要工具依赖项中定义的特定参数

## 核心操作（推荐使用）
* click_element: 按索引点击元素
  示例: click_element(index=33)
* input_text: 按索引输入文本
  示例: input_text(index=1, text="上海")

## 简易操作（适用于视觉理解模式）
* click: 点击元素 - 参数 element_description 描述要点击的元素
  示例: click(element_description="搜索按钮")
  示例: click(element_description="1月30日")
* type: 输入文本 - 参数 element_description 描述输入框，text 为要输入的文本
  示例: type(element_description="出发城市", text="上海")

## 辅助操作
* go_to_url: 转到特定 URL
* scroll_down/scroll_up: 按像素量向上/向下滚动
* send_keys: 发送键盘命令
* wait: 等待页面加载
* extract_content: 提取页面内容
* switch_tab/open_tab/close_tab: 标签页管理

注意：使用元素索引时，请参考当前浏览器状态中显示的元素编号。
"""

Context = TypeVar("Context")


class BrowserUseTool(BaseTool, Generic[Context]):
    name: str = "browser_use"
    description: str = _BROWSER_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_url",
                    "click_element",
                    "input_text",
                    "click",
                    "type",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "send_keys",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "go_back",
                    "web_search",
                    "wait",
                    "extract_content",
                    "switch_tab",
                    "open_tab",
                    "close_tab",
                ],
                "description": "要执行的浏览器操作。推荐使用 click_element（按索引点击）和 input_text（按索引输入）；视觉模式下可使用 click 和 type（通过元素描述）",
            },
            "url": {
                "type": "string",
                "description": "用于 'go_to_url' 或 'open_tab' 操作的 URL",
            },
            "index": {
                "type": "integer",
                "description": "用于 'click_element'、'input_text'、'get_dropdown_options' 或 'select_dropdown_option' 操作的元素索引",
            },
            "element_description": {
                "type": "string",
                "description": "用于 'click' 或 'type' 的元素描述（如：'搜索按钮'、'出发城市'、'1月30日'）",
            },
            "text": {
                "type": "string",
                "description": "用于 'input_text'、'type'、'scroll_to_text' 或 'select_dropdown_option' 操作的文本",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "用于 'scroll_down' 或 'scroll_up' 操作的滚动像素数（正数向下，负数向上）",
            },
            "tab_id": {
                "type": "integer",
                "description": "用于 'switch_tab' 操作的标签页 ID",
            },
            "query": {
                "type": "string",
                "description": "用于 'web_search' 操作的搜索查询",
            },
            "goal": {
                "type": "string",
                "description": "用于 'extract_content' 操作的提取目标",
            },
            "keys": {
                "type": "string",
                "description": "用于 'send_keys' 操作要发送的按键",
            },
            "seconds": {
                "type": "integer",
                "description": "用于 'wait' 操作要等待的秒数",
            },
        },
        "required": ["action"],
        "dependencies": {
            "go_to_url": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "click": ["element_description"],
            "type": ["element_description", "text"],
            "switch_tab": ["tab_id"],
            "open_tab": ["url"],
            "scroll_down": ["scroll_amount"],
            "scroll_up": ["scroll_amount"],
            "scroll_to_text": ["text"],
            "send_keys": ["keys"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "go_back": [],
            "web_search": ["query"],
            "wait": ["seconds"],
            "extract_content": ["goal"],
        },
    }

    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    browser: Optional[BrowserUseBrowser] = Field(default=None, exclude=True)
    context: Optional[BrowserContext] = Field(default=None, exclude=True)
    dom_service: Optional[DomService] = Field(default=None, exclude=True)
    web_search_tool: WebSearch = Field(default_factory=WebSearch, exclude=True)

    # Context for generic functionality
    tool_context: Optional[Context] = Field(default=None, exclude=True)

    llm: Optional[LLM] = Field(default_factory=LLM)

    @field_validator("parameters", mode="before")
    def validate_parameters(cls, v: dict, info: ValidationInfo) -> dict:
        if not v:
            raise ValueError("Parameters cannot be empty")
        return v

    async def _ensure_browser_initialized(self) -> BrowserContext:
        """确保浏览器和上下文已初始化。"""
        if self.browser is None:
            browser_config_kwargs = {"headless": False, "disable_security": True}

            if config.browser_config:
                from browser_use.browser.browser import ProxySettings

                # 处理代理设置。
                if config.browser_config.proxy and config.browser_config.proxy.server:
                    browser_config_kwargs["proxy"] = ProxySettings(
                        server=config.browser_config.proxy.server,
                        username=config.browser_config.proxy.username,
                        password=config.browser_config.proxy.password,
                    )

                browser_attrs = [
                    "headless",
                    "disable_security",
                    "extra_chromium_args",
                    "chrome_instance_path",
                    "wss_url",
                    "cdp_url",
                ]

                for attr in browser_attrs:
                    value = getattr(config.browser_config, attr, None)
                    if value is not None:
                        if not isinstance(value, list) or value:
                            browser_config_kwargs[attr] = value

            self.browser = BrowserUseBrowser(BrowserConfig(**browser_config_kwargs))

        if self.context is None:
            context_config = BrowserContextConfig()

            # 如果配置中有上下文配置，则使用它。
            if (
                config.browser_config
                and hasattr(config.browser_config, "new_context_config")
                and config.browser_config.new_context_config
            ):
                context_config = config.browser_config.new_context_config

            self.context = await self.browser.new_context(context_config)
            self.dom_service = DomService(await self.context.get_current_page())

            # 注入反检测脚本，绕过 WhaleGuard 等防护机制
            await self._inject_stealth_scripts()

        return self.context

    async def _inject_stealth_scripts(self) -> None:
        """注入反检测脚本以绕过网站的自动化检测。"""
        try:
            page = await self.context.get_current_page()

            # 注入自定义 User-Agent 和 navigator 覆盖
            stealth_js = """
            // 覆盖 navigator.webdriver 属性
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 覆盖 chrome 对象
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // 覆盖 permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
            );

            // 覆盖 plugins 数组
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // 覆盖 languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });

            // 覆盖 webgl vendor 信息
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter(parameter);
            };
            """
            await page.evaluate(stealth_js)
            logger.info("🕵️ Anti-detection stealth scripts injected successfully")
        except Exception as e:
            logger.warning(f"⚠️ Failed to inject stealth scripts: {e}")

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        element_description: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        """
        执行指定的浏览器操作。

        Args:
            action: 要执行的浏览器操作
            url: 用于导航或新标签页的 URL
            index: 用于点击或输入操作的元素索引
            text: 用于输入操作或搜索查询的文本
            element_description: 用于 'click' 或 'type' 的元素描述
            scroll_amount: 用于滚动操作的滚动像素数
            tab_id: 用于 switch_tab 操作的标签页 ID
            query: 用于 Google 搜索的搜索查询
            goal: 用于内容提取的提取目标
            keys: 用于键盘操作要发送的按键
            seconds: 要等待的秒数
            **kwargs: 其他参数

        Returns:
            包含操作输出或错误的 ToolResult
        """
        async with self.lock:
            try:
                context = await self._ensure_browser_initialized()

                # 从配置中获取最大内容长度
                max_content_length = getattr(
                    config.browser_config, "max_content_length", 2000
                )

                # 导航操作
                if action == "go_to_url":
                    if not url:
                        return ToolResult(
                            error="URL is required for 'go_to_url' action"
                        )

                    # 检测并修正携程机票 URL 中的过期日期
                    original_url = url
                    if "flights.ctrip.com" in url and "date=" in url:
                        date_match = re.search(r'date=(\d{4})-(\d{2})-(\d{2})', url)
                        if date_match:
                            try:
                                url_date = datetime.strptime(date_match.group(0)[5:], '%Y-%m-%d').date()
                                today = datetime.now().date()
                                if url_date < today:
                                    # 日期在过去，自动修正为当前年份
                                    corrected_date = url_date.replace(year=today.year)
                                    # 如果修正后仍在过去，使用明年
                                    if corrected_date < today:
                                        corrected_date = corrected_date.replace(year=today.year + 1)
                                    url = url.replace(date_match.group(0), f"date={corrected_date.strftime('%Y-%m-%d')}")
                                    logger.warning(f"[browser] 自动修正过期日期: {date_match.group(0)[5:]} -> {corrected_date.strftime('%Y-%m-%d')}")
                            except ValueError:
                                pass  # 日期解析失败，保持原 URL

                    page = await context.get_current_page()
                    await page.goto(url)
                    await page.wait_for_load_state()

                    if url != original_url:
                        return ToolResult(output=f"Navigated to {url} (日期已自动修正)")
                    return ToolResult(output=f"Navigated to {url}")

                elif action == "click":
                    """
                    点击元素（通过文字描述查找），不依赖 index。
                    支持通过元素文本、标签内容等描述来定位元素。
                    """
                    if not element_description:
                        return ToolResult(error="element_description is required for 'click' action")
                    try:
                        page = await context.get_current_page()
                        # 尝试多种选择器策略
                        # 1. 按文本查找可见元素
                        try:
                            locator = page.get_by_role("button", name=element_description, exact=False)
                            if await locator.count() > 0:
                                await locator.first.click()
                                return ToolResult(output=f"Clicked element: {element_description}")
                        except Exception:
                            pass
                        # 2. 按文本查找链接
                        try:
                            locator = page.get_by_role("link", name=element_description, exact=False)
                            if await locator.count() > 0:
                                await locator.first.click()
                                return ToolResult(output=f"Clicked link: {element_description}")
                        except Exception:
                            pass
                        # 3. 按通用文本定位
                        try:
                            locator = page.get_by_text(element_description, exact=False)
                            if await locator.count() > 0:
                                await locator.first.click()
                                return ToolResult(output=f"Clicked element by text: {element_description}")
                        except Exception:
                            pass
                        # 4. 按占位符（placeholder）查找 input
                        try:
                            locator = page.get_by_placeholder(element_description)
                            if await locator.count() > 0:
                                await locator.first.click()
                                return ToolResult(output=f"Clicked input with placeholder: {element_description}")
                        except Exception:
                            pass
                        # 5. 按标签名查找
                        try:
                            locator = page.locator(element_description)
                            if await locator.count() > 0:
                                await locator.first.click()
                                return ToolResult(output=f"Clicked element by selector: {element_description}")
                        except Exception:
                            pass
                        return ToolResult(output=f"Could not find element matching '{element_description}', but continuing")
                    except Exception as e:
                        return ToolResult(error=f"Failed to click element '{element_description}': {str(e)}")

                elif action == "type":
                    """
                    输入文本（通过文字描述查找输入框），不依赖 index。
                    """
                    if not element_description or not text:
                        return ToolResult(error="element_description and text are required for 'type' action")
                    try:
                        page = await context.get_current_page()
                        # 1. 先点击目标元素
                        clicked = False
                        try:
                            locator = page.get_by_placeholder(element_description)
                            if await locator.count() > 0:
                                await locator.first.click()
                                clicked = True
                        except Exception:
                            pass
                        if not clicked:
                            try:
                                locator = page.get_by_role("textbox", name=element_description, exact=False)
                                if await locator.count() > 0:
                                    await locator.first.click()
                                    clicked = True
                            except Exception:
                                pass
                        if not clicked:
                            try:
                                locator = page.get_by_label(element_description, exact=False)
                                if await locator.count() > 0:
                                    await locator.first.click()
                                    clicked = True
                            except Exception:
                                pass
                        if not clicked:
                            try:
                                locator = page.get_by_text(element_description, exact=False)
                                if await locator.count() > 0:
                                    await locator.first.click()
                                    clicked = True
                            except Exception:
                                pass
                        # 2. 输入文本
                        await page.keyboard.type(text)
                        return ToolResult(output=f"Typed '{text}' into element: {element_description}")
                    except Exception as e:
                        return ToolResult(error=f"Failed to type into element '{element_description}': {str(e)}")

                elif action == "go_back":
                    await context.go_back()
                    return ToolResult(output="Navigated back")

                elif action == "refresh":
                    await context.refresh_page()
                    return ToolResult(output="Refreshed current page")

                elif action == "web_search":
                    if not query:
                        return ToolResult(
                            error="Query is required for 'web_search' action"
                        )
                    # 执行网页搜索并直接返回结果，无需浏览器导航
                    search_response = await self.web_search_tool.execute(
                        query=query, fetch_content=True, num_results=1
                    )
                    # 导航到第一个搜索结果
                    first_search_result = search_response.results[0]
                    url_to_navigate = first_search_result.url

                    page = await context.get_current_page()
                    await page.goto(url_to_navigate)
                    await page.wait_for_load_state()

                    return search_response

                # 元素交互操作
                elif action == "click_element":
                    if index is None:
                        return ToolResult(
                            error="Index is required for 'click_element' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    download_path = await context._click_element_node(element)
                    output = f"Clicked element at index {index}"
                    if download_path:
                        output += f" - Downloaded file to {download_path}"
                    return ToolResult(output=output)

                elif action == "input_text":
                    if index is None or not text:
                        return ToolResult(
                            error="Index and text are required for 'input_text' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    await context._input_text_element_node(element, text)
                    return ToolResult(
                        output=f"Input '{text}' into element at index {index}"
                    )

                elif action == "scroll_down" or action == "scroll_up":
                    direction = 1 if action == "scroll_down" else -1
                    amount = (
                        scroll_amount
                        if scroll_amount is not None
                        else context.config.browser_window_size["height"]
                    )
                    await context.execute_javascript(
                        f"window.scrollBy(0, {direction * amount});"
                    )
                    return ToolResult(
                        output=f"Scrolled {'down' if direction > 0 else 'up'} by {amount} pixels"
                    )

                elif action == "scroll_to_text":
                    if not text:
                        return ToolResult(
                            error="Text is required for 'scroll_to_text' action"
                        )
                    page = await context.get_current_page()
                    try:
                        locator = page.get_by_text(text, exact=False)
                        await locator.scroll_into_view_if_needed()
                        return ToolResult(output=f"Scrolled to text: '{text}'")
                    except Exception as e:
                        return ToolResult(error=f"Failed to scroll to text: {str(e)}")

                elif action == "send_keys":
                    if not keys:
                        return ToolResult(
                            error="Keys are required for 'send_keys' action"
                        )
                    page = await context.get_current_page()
                    await page.keyboard.press(keys)
                    return ToolResult(output=f"Sent keys: {keys}")

                elif action == "get_dropdown_options":
                    if index is None:
                        return ToolResult(
                            error="Index is required for 'get_dropdown_options' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    page = await context.get_current_page()
                    options = await page.evaluate(
                        """
                        (xpath) => {
                            const select = document.evaluate(xpath, document, null,
                                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                            if (!select) return null;
                            return Array.from(select.options).map(opt => ({
                                text: opt.text,
                                value: opt.value,
                                index: opt.index
                            }));
                        }
                    """,
                        element.xpath,
                    )
                    return ToolResult(output=f"Dropdown options: {options}")

                elif action == "select_dropdown_option":
                    if index is None or not text:
                        return ToolResult(
                            error="Index and text are required for 'select_dropdown_option' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    page = await context.get_current_page()
                    await page.select_option(element.xpath, label=text)
                    return ToolResult(
                        output=f"Selected option '{text}' from dropdown at index {index}"
                    )

                # 内容提取操作
                elif action == "extract_content":
                    if not goal:
                        return ToolResult(
                            error="Goal is required for 'extract_content' action"
                        )

                    page = await context.get_current_page()
                    import markdownify

                    content = markdownify.markdownify(await page.content())

                    prompt = f"""\
Your task is to extract the content of the page. You will be given a page and a goal, and you should extract all relevant information around this goal from the page. If the goal is vague, summarize the page. Respond in json format.
Extraction goal: {goal}

Page content:
{content[:max_content_length]}
"""
                    messages = [{"role": "system", "content": prompt}]

                    # 定义提取函数模式
                    extraction_function = {
                        "type": "function",
                        "function": {
                            "name": "extract_content",
                            "description": "Extract specific information from a webpage based on a goal",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "extracted_content": {
                                        "type": "object",
                                        "description": "The content extracted from the page according to the goal",
                                        "properties": {
                                            "text": {
                                                "type": "string",
                                                "description": "Text content extracted from the page",
                                            },
                                            "metadata": {
                                                "type": "object",
                                                "description": "Additional metadata about the extracted content",
                                                "properties": {
                                                    "source": {
                                                        "type": "string",
                                                        "description": "Source of the extracted content",
                                                    }
                                                },
                                            },
                                        },
                                    }
                                },
                                "required": ["extracted_content"],
                            },
                        },
                    }

                    # 使用 LLM 通过必需的函数调用来提取内容
                    response = await self.llm.ask_tool(
                        messages,
                        tools=[extraction_function],
                        tool_choice="required",
                    )

                    if response and response.tool_calls:
                        args = json.loads(response.tool_calls[0].function.arguments)
                        extracted_content = args.get("extracted_content", {})
                        return ToolResult(
                            output=f"Extracted from page:\n{extracted_content}\n"
                        )

                    return ToolResult(output="No content was extracted from the page.")

                # 标签页管理操作
                elif action == "switch_tab":
                    if tab_id is None:
                        return ToolResult(
                            error="Tab ID is required for 'switch_tab' action"
                        )
                    await context.switch_to_tab(tab_id)
                    page = await context.get_current_page()
                    await page.wait_for_load_state()
                    return ToolResult(output=f"Switched to tab {tab_id}")

                elif action == "open_tab":
                    if not url:
                        return ToolResult(error="URL is required for 'open_tab' action")
                    await context.create_new_tab(url)
                    return ToolResult(output=f"Opened new tab with {url}")

                elif action == "close_tab":
                    await context.close_current_tab()
                    return ToolResult(output="Closed current tab")

                # 实用操作
                elif action == "wait":
                    seconds_to_wait = seconds if seconds is not None else 3
                    await asyncio.sleep(seconds_to_wait)
                    return ToolResult(output=f"Waited for {seconds_to_wait} seconds")

                else:
                    return ToolResult(error=f"Unknown action: {action}")

            except Exception as e:
                return ToolResult(error=f"Browser action '{action}' failed: {str(e)}")

    async def get_current_state(
        self, context: Optional[BrowserContext] = None
    ) -> ToolResult:
        """
        获取当前浏览器状态作为 ToolResult。
        如果未提供 context，则使用 self.context。
        """
        try:
            # 使用提供的 context 或回退到 self.context
            ctx = context or self.context
            if not ctx:
                return ToolResult(error="Browser context not initialized")

            state = await ctx.get_state()

            # 如果不存在，创建 viewport_info 字典
            viewport_height = 0
            if hasattr(state, "viewport_info") and state.viewport_info:
                viewport_height = state.viewport_info.height
            elif hasattr(ctx, "config") and hasattr(ctx.config, "browser_window_size"):
                viewport_height = ctx.config.browser_window_size.get("height", 0)

            # 为状态拍摄截图
            page = await ctx.get_current_page()

            await page.bring_to_front()
            await page.wait_for_load_state()

            screenshot = await page.screenshot(
                full_page=True, animations="disabled", type="jpeg", quality=100
            )

            screenshot = base64.b64encode(screenshot).decode("utf-8")
            screenshot_size_kb = len(screenshot) * 3 / 4 / 1024  # 估算图片大小（KB）

            # 获取可交互元素信息
            interactive_elements_str = (
                state.element_tree.clickable_elements_to_string()
                if state.element_tree
                else ""
            )
            element_count = interactive_elements_str.count("[") if interactive_elements_str else 0

            # 调试信息
            logger.info(f"🌐 Browser state captured: URL={state.url}, Title={state.title}")
            logger.info(f"📸 Screenshot size: {screenshot_size_kb:.2f} KB (base64)")
            logger.info(f"🔍 Interactive elements detected: {element_count}")
            if element_count == 0:
                logger.warning(f"⚠️ No interactive elements found - page may be empty or not loaded")
            if interactive_elements_str:
                # 显示前几个元素作为示例
                lines = interactive_elements_str.split("\n")[:5]
                preview = "\n".join(lines)
                logger.debug(f"🔍 Elements preview (first 5):\n{preview}")

            # 构建包含所有必需字段的状态信息
            state_info = {
                "url": state.url,
                "title": state.title,
                "tabs": [tab.model_dump() for tab in state.tabs],
                "help": "[0], [1], [2], etc., represent clickable indices corresponding to the elements listed. Clicking on these indices will navigate to or interact with the respective content behind them.",
                "interactive_elements": interactive_elements_str,
                "scroll_info": {
                    "pixels_above": getattr(state, "pixels_above", 0),
                    "pixels_below": getattr(state, "pixels_below", 0),
                    "total_height": getattr(state, "pixels_above", 0)
                    + getattr(state, "pixels_below", 0)
                    + viewport_height,
                },
                "viewport_height": viewport_height,
            }

            return ToolResult(
                output=json.dumps(state_info, indent=4, ensure_ascii=False),
                base64_image=screenshot,
            )
        except Exception as e:
            return ToolResult(error=f"Failed to get browser state: {str(e)}")

    async def cleanup(self):
        """清理浏览器资源。"""
        async with self.lock:
            if self.context is not None:
                await self.context.close()
                self.context = None
                self.dom_service = None
            if self.browser is not None:
                await self.browser.close()
                self.browser = None

    def __del__(self):
        """确保在对象销毁时进行清理。"""
        if self.browser is not None or self.context is not None:
            try:
                asyncio.run(self.cleanup())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.cleanup())
                loop.close()

    @classmethod
    def create_with_context(cls, context: Context) -> "BrowserUseTool[Context]":
        """创建具有特定上下文的 BrowserUseTool 的工厂方法。"""
        tool = cls()
        tool.tool_context = context
        return tool
