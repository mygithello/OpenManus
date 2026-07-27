---
name: spec-browser-use
module: agent
description: 浏览器自动化操作工具规范
---

# 浏览器自动化工具 (browser_use)

## 功能概述
基于 browser-use 和 Playwright 的浏览器自动化操作工具，支持导航、点击、输入、滚动、提取内容、标签页管理等操作。

## 相关源文件
| 文件 | 说明 |
|------|------|
| app/tool/browser_use_tool.py | 浏览器操作工具核心实现 |
| app/agent/manus.py | Manus 代理中的浏览器集成 |
| app/agent/browser.py | 浏览器代理实现 |
| app/prompt/browser.py | 浏览器操作提示模板 |

## 核心操作
| 操作 | 说明 | 参数 |
|------|------|------|
| go_to_url | 导航到 URL | url |
| click_element | 按索引点击 | index |
| input_text | 按索引输入 | index, text |
| click | 按描述点击 | element_description |
| type | 按描述输入 | element_description, text |
| scroll_down/up | 滚动 | scroll_amount |
| extract_content | 提取内容 | goal |
| web_search | 搜索导航 | query |

## 更新历史
| 日期 | 版本 | 变更说明 |
|------|------|----------|
|  | v0.3.0-gui | ① 新增 click/type 按描述操作（gui-plus）；② 修复全屏截图坐标与 DOM 不对齐问题；③ 增加 --start-maximized 窗口最大化；④ 优化日期自动修正逻辑（仅跨年修正）；⑤ 修复反检测脚本注入冲突；⑥ 修复 WebGL getParameter Illegal invocation |
