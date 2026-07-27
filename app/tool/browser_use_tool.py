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

    # 调试标志：每次获取状态时保存 HTML 和截图快照
    debug_save_snapshot: bool = False

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

            # 确保 --start-maximized 被添加，让浏览器窗口最大化
            self._ensure_maximized(browser_config_kwargs)

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
        """注入反检测脚本以绕过网站的自动化检测。
        browser_use 的 add_init_script 已注入部分脚本（webdriver/languages/plugins/chrome/permissions），
        本方法补充注入 browser_use 未覆盖的脚本（如 WebGL vendor），并优先保留已注入的部分。
        """
        try:
            page = await self.context.get_current_page()

            # 注入补充的反检测脚本，使用 try/catch 避免与 add_init_script 冲突
            # 注意：WebGL 部分使用原函数引用 + .call，避免 Playwright 的 "Illegal invocation" 错误
            stealth_js = """
            try {
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            } catch(e) {}

            try {
                window.chrome = window.chrome || {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };
            } catch(e) {}

            try {
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    originalQuery(parameters)
                );
            } catch(e) {}

            try {
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
            } catch(e) {}

            try {
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en']
                });
            } catch(e) {}

            // WebGL vendor 覆盖 — 保存原型方法引用，使用 Function.prototype.call 避免 Illegal invocation
            try {
                var getParamOrig = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function getParameterOverride(parameter) {
                    try {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    } catch(e) {}
                    try { return Function.prototype.call.call(getParamOrig, this, parameter); } catch(e) {}
                    try { return getParamOrig.call(this, parameter); } catch(e) {}
                    return null;
                };
            } catch(e) {}
            """
            await page.evaluate(stealth_js)
            logger.info("🕵️ Anti-detection stealth scripts injected successfully")
        except Exception as e:
            logger.warning(f"⚠️ Failed to inject stealth scripts: {e}")

    @staticmethod
    def _ensure_maximized(browser_config_kwargs: dict) -> None:
        """确保浏览器窗口启动时最大化。"""
        max_flag = "--start-maximized"
        if "extra_chromium_args" not in browser_config_kwargs:
            browser_config_kwargs["extra_chromium_args"] = []
        args = browser_config_kwargs["extra_chromium_args"]
        if isinstance(args, list) and max_flag not in args:
            args.append(max_flag)

    async def _save_debug_snapshot(self, label: str) -> None:
        """保存当前页面的 HTML 和截图到调试目录。"""
        if not self.debug_save_snapshot:
            return
        try:
            import os
            from datetime import datetime

            page = await self.context.get_current_page()
            # 保存到项目根目录的 debug_snapshots/
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            debug_dir = os.path.join(project_root, "debug_snapshots")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:23]
            safe_label = label.replace("/", "_").replace(" ", "_")[:60]

            # 保存 HTML
            html = await page.content()
            html_path = os.path.join(debug_dir, f"{timestamp}_{safe_label}.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"💾 Debug HTML saved ({len(html)} chars) → {html_path}")

            # 保存截图
            screenshot_path = os.path.join(debug_dir, f"{timestamp}_{safe_label}.png")
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"📸 Debug screenshot saved → {screenshot_path}")

            # 保存页面文本
            text = await page.evaluate("document.body ? document.body.innerText : ''")
            text_path = os.path.join(debug_dir, f"{timestamp}_{safe_label}.txt")
            with open(text_path, "w", encoding="utf-8") as f:
                f.write(text[:5000])
            logger.info(f"📄 Debug text saved ({len(text)} chars) → {text_path}")

            # 检查 WhaleGuard/验证码特征
            if "whaleguard" in html.lower() or "whale guard" in html.lower():
                logger.warning("⚠️ DETECTED: Page blocked by WhaleGuard anti-bot protection!")
            elif "captcha" in html.lower() or "verify" in html.lower():
                logger.warning("⚠️ DETECTED: Page has captcha/verification challenge!")
            elif "524" in html[:500] or "cloudflare" in html.lower():
                logger.warning("⚠️ DETECTED: Cloudflare/524 challenge detected!")
            else:
                logger.info("✅ Page appears to be normal content")
        except Exception as e:
            logger.warning(f"⚠️ Failed to save debug snapshot: {e}")

    async def _execute_vision_action(
        self, context: BrowserContext, vision_instruction: str, action_hint: str = "click"
    ) -> ToolResult:
        """
        使用视觉模型分析截图，执行坐标级别的浏览器操作。
        适用于动态元素（日期选择器、弹窗等）的点击和输入。

        工作流程：
        1. 截取当前页面的 viewport 截图
        2. 调用视觉模型分析截图并生成操作指令（CLICK/TYPE/SCROLL 等）
        3. 解析模型返回的 JSON，执行坐标操作
        """
        try:
            from openai import AsyncOpenAI
            import os

            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            # 1. 截取 viewport 截图（不截全页，确保坐标正确）
            screenshot_bytes = await page.screenshot(
                type="png",
                full_page=False,
            )
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            image_data_url = f"data:image/png;base64,{screenshot_base64}"

            logger.info(f"[vision] Taking screenshot for {action_hint} | instruction: {vision_instruction}")

            # 2. 构建视觉分析 prompt
            vision_system_prompt = """你是一个顶级的AI视觉操作代理。分析屏幕截图，理解用户指令，返回精确的GUI操作。

## 可用操作
### CLICK
{"action": "CLICK", "parameters": {"x": <整数坐标>, "y": <整数坐标>, "description": "<点击目标的描述>"}}

### TYPE
{"action": "TYPE", "parameters": {"x": <输入框中心x>, "y": <输入框中心y>, "text": "<输入文本>", "needs_enter": false, "description": "<输入框描述>"}}

### SCROLL
{"action": "SCROLL", "parameters": {"direction": "down/up", "amount": "small/medium/large"}}

### KEY_PRESS
{"action": "KEY_PRESS", "parameters": {"key": "enter/esc/tab"}}

### FINISH / FAIL
{"action": "FINISH", "parameters": {"message": "..."}}
{"action": "FAIL", "parameters": {"reason": "..."}}

## 重要规则
- 坐标必须是目标元素的中心位置（根据截图中的实际位置估算）
- 输入框的坐标应该是输入框内部的中心
- 返回内容必须是严格的 JSON 格式，不要包含 markdown 代码块标记
"""

            # 3. 调用视觉模型
            vision_api_key = os.getenv("DASHSCOPE_API_KEY") or config.llm.get("default", {}).get("api_key", "")
            vision_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

            client = AsyncOpenAI(api_key=vision_api_key, base_url=vision_base_url)

            messages = [
                {"role": "system", "content": vision_system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "text", "text": vision_instruction},
                    ],
                },
            ]

            logger.info(f"[vision] Calling vision model with: {vision_instruction}")

            completion = await client.chat.completions.create(
                model="qwen-vl-max",  # 使用 qwen-vl-max 视觉模型
                messages=messages,
            )

            response_content = completion.choices[0].message.content
            logger.info(f"[vision] Model response: {response_content[:200]}")

            # 4. 解析 JSON 响应
            # 处理模型输出中的常见格式错误
            fixed = response_content
            # 修复 {"x": 139, 675} -> {"x": 139, "y": 675}
            fixed = re.sub(r'"x":\s*(\d+),\s*(\d+)\s*[,}]', lambda m: f'"x": {m.group(1)}, "y": {m.group(2)}' + (',' if m.group(0).endswith(',') else '}'), fixed)
            # 修复 {"x": [139, 675]} -> {"x": 139, "y": 675}
            fixed = re.sub(r'"x":\s*\[(\d+),\s*(\d+)\]', r'"x": \1, "y": \2', fixed)

            json_match = re.search(r'\{[\s\S]*\}', fixed)
            if not json_match:
                return ToolResult(error=f"[vision] Failed to parse JSON from model response: {response_content[:200]}")

            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError as e:
                json_str = json_match.group()
                open_braces = json_str.count('{')
                close_braces = json_str.count('}')
                if open_braces > close_braces:
                    try:
                        result = json.loads(json_str + '}' * (open_braces - close_braces))
                    except json.JSONDecodeError:
                        return ToolResult(error=f"[vision] JSON parse failed: {e} | response: {response_content[:200]}")
                else:
                    return ToolResult(error=f"[vision] JSON parse failed: {e} | response: {response_content[:200]}")

            action_type = result.get("action", "").strip().upper()
            params = result.get("parameters", {})
            thought = result.get("thought", "")

            # 如果 action 类型缺失但包含坐标，自动推断
            if not action_type:
                if result.get("x") is not None or params.get("x") is not None:
                    action_type = "TYPE" if result.get("text") or params.get("text") else "CLICK"
                    if not params:
                        params = result

            logger.info(f"[vision] Decision: action={action_type}, thought={thought}")

            # 5. 执行操作
            if action_type == "CLICK":
                x = params.get("x")
                y = params.get("y")
                description = params.get("description", "")

                # 处理坐标格式
                if isinstance(x, list) and len(x) >= 2:
                    x, y = x[0], x[1]
                if isinstance(y, list) and len(y) >= 1:
                    y = y[0]

                if x is None or y is None:
                    return ToolResult(error=f"[vision] CLICK missing coordinates: {params}")
                try:
                    x, y = int(x), int(y)
                except (ValueError, TypeError):
                    return ToolResult(error=f"[vision] CLICK invalid coordinates: x={x}, y={y}")

                logger.info(f"[vision] CLICK at ({x}, {y}): {description}")
                await page.mouse.click(x, y)
                await asyncio.sleep(0.5)
                return ToolResult(output=f"[vision] Clicked ({x}, {y}): {description}")

            elif action_type == "TYPE":
                text_to_type = params.get("text", "")
                needs_enter = params.get("needs_enter", False)
                description = params.get("description", "input")

                if not text_to_type:
                    return ToolResult(error="[vision] TYPE missing text")

                x = params.get("x")
                y = params.get("y")

                if isinstance(x, list) and len(x) >= 2:
                    x, y = x[0], x[1]
                if isinstance(y, list) and len(y) >= 1:
                    y = y[0]

                if x is not None and y is not None:
                    try:
                        x, y = int(x), int(y)
                    except (ValueError, TypeError):
                        pass
                    logger.info(f"[vision] TYPE: click ({x}, {y}) then type '{text_to_type}'")
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Control+a")
                    await asyncio.sleep(0.1)

                await page.keyboard.type(text_to_type)
                if needs_enter:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(0.3)
                return ToolResult(output=f"[vision] Typed '{text_to_type}' into {description}")

            elif action_type == "SCROLL":
                direction = params.get("direction", "down")
                amount = params.get("amount", "medium")
                pixels = {"small": 100, "medium": 300, "large": 600}.get(amount, 300)
                await page.mouse.wheel(0, -pixels if direction == "up" else pixels)
                return ToolResult(output=f"[vision] Scrolled {direction} {amount}")

            elif action_type == "KEY_PRESS":
                key = params.get("key", "")
                if key:
                    await page.keyboard.press(key)
                    return ToolResult(output=f"[vision] Pressed key: {key}")
                return ToolResult(error="[vision] KEY_PRESS missing key")

            elif action_type in ("FINISH", "FAIL"):
                msg = params.get("message") or params.get("reason", "")
                return ToolResult(
                    output=f"[vision] {'Finished' if action_type == 'FINISH' else 'Failed'}: {msg}"
                    if action_type == "FINISH"
                    else None,
                    error=f"[vision] Failed: {msg}" if action_type == "FAIL" else None,
                )

            return ToolResult(error=f"[vision] Unknown action: {action_type}")

        except Exception as e:
            logger.error(f"[vision] Execution failed: {e}")
            return ToolResult(error=f"[vision] Execution error: {str(e)}")

    async def _execute_smart_click(
        self, context: BrowserContext, element_description: str
    ) -> ToolResult:
        """
        智能点击：通过 JavaScript 提取可见可点击元素，用 LLM 匹配后坐标点击。
        比 Playwright 的文本匹配更灵活，能处理动态生成的元素。
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            # 1. 获取视窗内可见的可点击元素信息（含 bounding box）
            elements_info = await page.evaluate("""
                () => {
                    const elements = [];
                    const vh = window.innerHeight;
                    const vw = window.innerWidth;

                    // 收集标准可点击元素
                    const clickables = document.querySelectorAll(
                        'button, a, [onclick], [role="button"], input[type="submit"], ' +
                        'input[type="button"], [class*="btn"], [class*="button"], ' +
                        'label, select, summary, [tabindex]:not([tabindex="-1"])'
                    );
                    clickables.forEach((el) => {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.y >= 0 && r.y < vh && r.x >= 0 && r.x < vw) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                text: (el.innerText || el.value || el.placeholder || '').trim().substring(0, 80),
                                type: el.type || '',
                                ariaLabel: el.getAttribute('aria-label') || '',
                                rect: { x: r.x, y: r.y, w: r.width, h: r.height }
                            });
                        }
                    });

                    // 收集日期/日历元素
                    document.querySelectorAll(
                        'div[class*="date"], div[class*="day"], span[class*="date"], ' +
                        'span[class*="day"], td[class*="date"], td[class*="day"], ' +
                        'div[class*="calendar"], [class*="DatePicker"]'
                    ).forEach((el) => {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.y >= 0 && r.y < vh && r.x >= 0 && r.x < vw) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                text: (el.innerText || '').trim().substring(0, 30),
                                type: 'date-element',
                                ariaLabel: el.getAttribute('aria-label') || '',
                                rect: { x: r.x, y: r.y, w: r.width, h: r.height }
                            });
                        }
                    });

                    return elements.slice(0, 150);
                }
            """)

            if not elements_info:
                logger.warning("[smart] No clickable elements found in viewport")
                # 回退到视觉模型
                return await self._execute_vision_action(context, f"点击{element_description}", "click")

            # 2. 用 LLM 匹配最佳元素
            elements_text = "\n".join([
                f"[{i}] <{e['tag']}> text='{e['text']}' aria='{e.get('ariaLabel', '')}'"
                for i, e in enumerate(elements_info)
            ])

            prompt = f"""根据用户描述找到最匹配的可点击元素。

用户描述: {element_description}

页面元素:
{elements_text}

只返回最佳元素索引数字（0-{len(elements_info)-1}）。如果完全不匹配返回 -1。"""

            response = await self.llm.ask(
                messages=[{"role": "user", "content": prompt}],
                system_msgs=[{"role": "system", "content": "你是精确的页面元素匹配器，只返回数字索引。"}]
            )

            match = re.search(r'-?\d+', response)
            idx = int(match.group()) if match else -1

            if idx < 0 or idx >= len(elements_info):
                logger.warning(f"[smart] LLM no match for '{element_description}', falling back to vision")
                return await self._execute_vision_action(context, f"点击{element_description}", "click")

            target = elements_info[idx]
            click_x = target['rect']['x'] + target['rect']['w'] / 2
            click_y = target['rect']['y'] + target['rect']['h'] / 2

            logger.info(f"[smart] Click '{element_description}' → [{idx}] {target['tag']} '{target['text'][:30]}' at ({click_x:.0f}, {click_y:.0f})")

            await page.mouse.click(click_x, click_y)
            await asyncio.sleep(0.5)
            return ToolResult(output=f"[smart] Clicked [{idx}] {target['tag']} '{target['text'][:40]}'")

        except Exception as e:
            logger.error(f"[smart] Click failed: {e}")
            return await self._execute_vision_action(context, f"点击{element_description}", "click")

    async def _execute_smart_input(
        self, context: BrowserContext, element_description: str, text: str
    ) -> ToolResult:
        """
        智能输入：通过 JavaScript 提取可见输入框，用 LLM 匹配后坐标输入。
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            # 1. 获取视窗内可见输入框信息
            inputs_info = await page.evaluate("""
                () => {
                    const inputs = [];
                    const vh = window.innerHeight;

                    const selectors = 'input[type="text"], input[type="search"], ' +
                        'input:not([type]), textarea, [contenteditable="true"], ' +
                        '[role="textbox"], [role="combobox"], [role="searchbox"]';

                    document.querySelectorAll(selectors).forEach((el) => {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.y >= 0 && r.y < vh) {
                            const parentLabel = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
                            const labelText = parentLabel ? parentLabel.innerText.trim() : '';

                            // 获取父容器文本
                            const parent = el.closest('div, label, li, form, fieldset');
                            const parentText = parent ? parent.innerText.split('\\n')[0].trim().substring(0, 40) : '';

                            inputs.push({
                                tag: el.tagName.toLowerCase(),
                                placeholder: el.placeholder || '',
                                value: el.value || '',
                                name: el.name || '',
                                id: el.id || '',
                                className: el.className || '',
                                ariaLabel: el.getAttribute('aria-label') || '',
                                labelText: labelText,
                                parentText: parentText,
                                rect: { x: r.x, y: r.y, w: r.width, h: r.height }
                            });
                        }
                    });
                    return inputs;
                }
            """)

            if not inputs_info:
                logger.warning("[smart] No input fields found in viewport")
                return await self._execute_vision_action(context, f"在{element_description}输入'{text}'", "type")

            # 2. 用 LLM 匹配最佳输入框
            inputs_text = "\n".join([
                f"[{i}] placeholder='{inp['placeholder']}' label='{inp['labelText']}' aria='{inp['ariaLabel']}' parent='{inp['parentText']}'"
                for i, inp in enumerate(inputs_info)
            ])

            prompt = f"""根据用户描述找到最匹配的输入框。

用户描述: {element_description}

输入框列表:
{inputs_text}

只返回最佳输入框索引数字（0-{len(inputs_info)-1}）。如果完全不匹配返回 -1。"""

            response = await self.llm.ask(
                messages=[{"role": "user", "content": prompt}],
                system_msgs=[{"role": "system", "content": "你是精确的输入框匹配器，只返回数字索引。"}]
            )

            match = re.search(r'-?\d+', response)
            idx = int(match.group()) if match else -1

            if idx < 0 or idx >= len(inputs_info):
                logger.warning(f"[smart] LLM no input match for '{element_description}', trying vision")
                return await self._execute_vision_action(context, f"在{element_description}输入'{text}'", "type")

            target = inputs_info[idx]
            click_x = target['rect']['x'] + target['rect']['w'] / 2
            click_y = target['rect']['y'] + target['rect']['h'] / 2

            logger.info(f"[smart] Input '{element_description}' → [{idx}] placeholder='{target['placeholder']}' at ({click_x:.0f}, {click_y:.0f})")

            # 点击 + 输入
            await page.mouse.click(click_x, click_y)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await page.keyboard.type(text)
            await asyncio.sleep(0.3)

            return ToolResult(output=f"[smart] Typed '{text}' into [{idx}] '{target['placeholder'] or target['labelText'] or element_description}'")

        except Exception as e:
            logger.error(f"[smart] Input failed: {e}")
            return await self._execute_vision_action(context, f"在{element_description}输入'{text}'", "type")

    # =====================
    # Tiered click / type
    # =====================

    async def _click(self, context: BrowserContext, element_description: str) -> ToolResult:
        """
        分层次点击：Playwright locator → 智能 HTML 匹配 → 视觉模型
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            logger.info(f"[click] Trying to click: '{element_description}'")

            # Tier 0: 日期类描述 → 直接视觉模型（更可靠）
            # 日期选择器中的元素动态渲染，Playwright 难以通过文本定位
            is_date_like = (
                element_description.isdigit() or
                bool(re.search(r'^\d+[日号]?$', element_description)) or
                bool(re.search(r'\d+月\d+[日号]?', element_description)) or
                "日历" in element_description or
                "calendar" in element_description.lower()
            )

            if is_date_like:
                logger.info(f"[click] Date-like description, trying Playwright locator first")
                # 先尝试普通文本查找
                day_match = re.search(r'(\d+)', element_description)
                if day_match:
                    day_num = day_match.group(1)
                    try:
                        locator = page.locator(f"text={day_num}").last
                        if await locator.is_visible():
                            await locator.click()
                            await asyncio.sleep(0.5)
                            return ToolResult(output=f"[click] Playwright clicked date: {day_num}")
                    except Exception:
                        pass
                # 回退到视觉模型
                return await self._execute_vision_action(context, f"点击日历中的{element_description}", "click")

            # Tier 1: Playwright locator 精确匹配
            try:
                locator = page.get_by_text(element_description, exact=True)
                if await locator.count() > 0:
                    for i in range(await locator.count()):
                        el = locator.nth(i)
                        if await el.is_visible():
                            box = await el.bounding_box()
                            if box and box['y'] < 600:
                                cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                                logger.info(f"[click] Exact match: '{element_description}' at ({cx:.0f}, {cy:.0f})")
                                await page.mouse.click(cx, cy)
                                await asyncio.sleep(0.5)
                                return ToolResult(output=f"[click] Clicked '{element_description}'")

                # 包含文字匹配（限制文本长度防止误匹配）
                locator = page.get_by_text(element_description, exact=False)
                if await locator.count() > 0:
                    for i in range(min(await locator.count(), 10)):
                        el = locator.nth(i)
                        if await el.is_visible():
                            tc = await el.text_content()
                            if tc and len(tc.strip()) <= len(element_description) * 3:
                                box = await el.bounding_box()
                                if box and box['y'] < 600:
                                    cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                                    logger.info(f"[click] Partial match: '{tc[:30]}' at ({cx:.0f}, {cy:.0f})")
                                    await page.mouse.click(cx, cy)
                                    await asyncio.sleep(0.5)
                                    return ToolResult(output=f"[click] Clicked '{tc[:30]}'")
            except Exception:
                pass

            # Tier 2: Smart click (JavaScript + LLM)
            logger.info(f"[click] Playwright failed, trying smart click")
            return await self._execute_smart_click(context, element_description)

        except Exception as e:
            logger.error(f"[click] Error: {e}")
            return ToolResult(error=f"[click] Failed: {str(e)}")

    async def _type(self, context: BrowserContext, element_description: str, text: str) -> ToolResult:
        """
        分层次输入：Playwright locator → 智能 HTML 匹配 → 视觉模型
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            logger.info(f"[type] Trying to type '{text}' into '{element_description}'")

            # Tier 1: Playwright locator
            clicked = False
            click_x, click_y = None, None

            for strategy_fn in [
                lambda: page.get_by_placeholder(element_description),
                lambda: page.get_by_role("textbox", name=element_description, exact=False),
                lambda: page.get_by_label(element_description, exact=False),
                lambda: page.get_by_text(element_description, exact=False),
                lambda: page.locator(f'[aria-label*="{element_description}"], [data-label*="{element_description}"]'),
            ]:
                if clicked:
                    break
                try:
                    locator = strategy_fn()
                    if await locator.count() > 0:
                        el = locator.first
                        if await el.is_visible():
                            box = await el.bounding_box()
                            if box:
                                click_x, click_y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                                await page.mouse.click(click_x, click_y)
                                clicked = True
                except Exception:
                    pass

            if clicked and click_x is not None:
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(text)
                await asyncio.sleep(0.3)
                return ToolResult(output=f"[type] Typed '{text}' into '{element_description}'")

            # Tier 2: Smart input (JavaScript + LLM)
            logger.info(f"[type] Playwright failed, trying smart input")
            return await self._execute_smart_input(context, element_description, text)

        except Exception as e:
            logger.error(f"[type] Error: {e}")
            return ToolResult(error=f"[type] Failed: {str(e)}")

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
                    # 修正规则：仅当 URL 中日期年份与当前年份不同时，自动修正为当前年份
                    # 不修正同一年份的日期，即使已过期（可能是用户明确查询的历史日期）
                    original_url = url
                    if "flights.ctrip.com" in url and "date=" in url:
                        date_match = re.search(r'date=(\d{4})-(\d{2})-(\d{2})', url)
                        if date_match:
                            try:
                                url_date = datetime.strptime(date_match.group(0)[5:], '%Y-%m-%d').date()
                                today = datetime.now().date()
                                # 仅在年份不同时进行修正（如模板示例中的过期年份）
                                if url_date.year != today.year and url_date < today:
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

                    # 导航后保存调试快照
                    await self._save_debug_snapshot(f"navigate_{url[:50]}")

                    if url != original_url:
                        return ToolResult(output=f"Navigated to {url} (日期已自动修正)")
                    return ToolResult(output=f"Navigated to {url}")

                elif action == "click":
                    """
                    点击元素（通过文字描述查找）。
                    自动路由到最佳策略：Playwright → 智能 HTML 匹配 → 视觉模型。
                    """
                    if not element_description:
                        return ToolResult(error="element_description is required for 'click' action")
                    return await self._click(context, element_description)

                elif action == "type":
                    """
                    输入文本（通过文字描述查找输入框）。
                    自动路由到最佳策略：Playwright → 智能 HTML 匹配 → 视觉模型。
                    """
                    if not element_description:
                        return ToolResult(error="element_description is required for 'type' action")
                    if not text:
                        return ToolResult(error="text is required for 'type' action")
                    return await self._type(context, element_description, text)

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

                    page = await context.get_current_page()

                    # 检测点击前状态（确定是否点击日期相关元素）
                    elements_before = ""
                    try:
                        state_before = await context.get_state()
                        if state_before and state_before.element_tree:
                            elements_before = state_before.element_tree.clickable_elements_to_string()
                            if elements_before:
                                element_lines = elements_before.split("\n")
                                if index < len(element_lines):
                                    element_line = element_lines[index]
                                    if any(kw in element_line.lower() for kw in ["日期", "date", "出发", "departure", "calendar", "日历"]):
                                        logger.info(f"📅 Clicking date-related element (index {index}): {element_line[:100]}")
                    except Exception:
                        pass

                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    download_path = await context._click_element_node(element)

                    # 等待页面稳定
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass

                    # 如果是日期相关元素，等待日期选择器打开后检测元素变化
                    if elements_before:
                        await asyncio.sleep(1)
                        try:
                            state_after = await context.get_state()
                            if state_after and state_after.element_tree:
                                elements_after = state_after.element_tree.clickable_elements_to_string()
                                count_before = elements_before.count("[")
                                count_after = elements_after.count("[") if elements_after else 0
                                if abs(count_after - count_before) > 10:
                                    logger.info(f"📅 Element count changed after click: {count_before} -> {count_after} (date picker opened)")
                                    # 保存日期选择器打开后的 HTML
                                    html_content = await page.content()
                                    from pathlib import Path
                                    debug_dir = Path("debug_html")
                                    debug_dir.mkdir(exist_ok=True)
                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    filepath = debug_dir / f"{timestamp}_date_picker_opened.html"
                                    with open(filepath, "w", encoding="utf-8") as f:
                                        f.write(html_content)
                                    logger.info(f"💾 Saved date picker HTML to {filepath}")

                                    if elements_after:
                                        ef = debug_dir / f"{timestamp}_date_picker_elements.txt"
                                        with open(ef, "w", encoding="utf-8") as f:
                                            f.write(elements_after)
                                        logger.info(f"💾 Saved date picker elements to {ef}")
                                    await asyncio.sleep(1)
                        except Exception:
                            pass

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

            # 检查窗口大小
            viewport_height = 0
            if hasattr(state, "viewport_info") and state.viewport_info:
                viewport_height = state.viewport_info.height
            elif hasattr(ctx, "config") and hasattr(ctx.config, "browser_window_size"):
                viewport_height = ctx.config.browser_window_size.get("height", 0)

            # 保存调试快照
            await self._save_debug_snapshot(f"state_{state.title or 'untitled'}")

            # 使用 browser_use 内部已生成的截图（viewport 截图，与 DOM 坐标对齐）
            # 避免重新 full_page 截图导致高亮标签位置与页面元素不匹配
            if state.screenshot:
                screenshot = state.screenshot
            else:
                # 回退：手动截图（仅在无缓存截图时）
                page = await ctx.get_current_page()
                await page.bring_to_front()
                await page.wait_for_load_state()
                raw_screenshot = await page.screenshot(
                    full_page=False, animations="disabled", type="jpeg", quality=100
                )
                screenshot = base64.b64encode(raw_screenshot).decode("utf-8")

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

            # 自动保存 HTML 到调试目录（特别用于检测日期选择器等问题）
            try:
                page_src = await ctx.get_current_page()
                html_content = await page_src.content()
                from pathlib import Path
                debug_dir = Path("debug_html")
                debug_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                url_safe = (state.url or "").replace("https://", "").replace("http://", "")
                url_safe = re.sub(r'[?&=:/<>"|*\\]', '_', url_safe)[:50]
                html_path = debug_dir / f"{timestamp}_{url_safe}.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                logger.info(f"💾 Saved HTML ({len(html_content)} chars) to {html_path}")
                if element_count < 150 and "flights" in (state.url or ""):
                    elem_path = debug_dir / f"{timestamp}_elements.txt"
                    with open(elem_path, "w", encoding="utf-8") as f:
                        f.write(f"URL: {state.url}\nElement Count: {element_count}\n\n{interactive_elements_str}")
                    logger.info(f"💾 Saved elements info to {elem_path}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to save debug HTML: {e}")

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
