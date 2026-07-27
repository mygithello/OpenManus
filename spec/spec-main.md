# OpenManus 规格路由文档

> 最后更新: 

## 1. 项目概述

OpenManus 是一个基于 browser-use 和 Playwright 的 AI 代理框架，用于自动化 Web 浏览器任务。

## 2. 路由索引

### 2.1 模块路由

| 模块 | 说明 | 文档 |
|------|------|------|
| agent | 代理实现（Manus、BrowserAgent、ToolCallAgent 等） | modules/spec-agent.md |
| tool | 工具实现（BrowserUseTool、WebSearch 等） | - |
| config | 配置管理 | modules/spec-config.md |
| daytona | Daytona 云沙箱集成 | modules/spec-daytona.md |
| sandbox | 本地沙箱实现 | - |
| prompt | LLM 提示模板 | - |

### 2.2 功能点路由

| 功能点 | 说明 | 所属模块 | 文档 |
|--------|------|----------|------|
| browser-use | 浏览器自动化操作工具 | agent | features/spec-browser-use.md |
| model-auto-switch | 模型自动切换与降级机制 | agent | features/spec-model-auto-switch.md |
| daytona-sandbox | Daytona 云沙箱功能 | daytona | features/spec-daytona-sandbox.md |

## 3. 更新记录

| 日期 | 变更说明 |
|------|----------|
|  | 新增 browser-use feature spec；新增 model-auto-switch feature spec |
