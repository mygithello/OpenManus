---
name: spec-model-auto-switch
module: agent
description: 模型自动切换与降级机制规范
---

# 模型自动切换机制

## 功能概述
在视觉模型不可用时自动降级到文本模型，避免因配额耗尽导致的程序崩溃。

## 相关源文件
| 文件 | 说明 |
|------|------|
| app/llm.py | LLM 类，添加模型可用性追踪 |
| app/agent/manus.py | Manus 代理，运行时自动切换 |

## 核心逻辑

### 模型可用性追踪
- 类级别 `_unavailable_models: Set[str]` 跟踪不可用模型
- `mark_model_unavailable()` / `is_model_available()` 管理状态

### 重试策略
- `PermissionDeniedError(403)` - 不重试，直接标记不可用
- `AuthenticationError(401)` - 不重试
- `APIError` / `RateLimitError` - 正常重试（指数退避，最多6次）

### 视觉模型降级流程
1. `manus.py:think()` 切换视觉模型前检查 `is_model_available()`
2. 若不可用 - 跳过切换，使用文本模型 + DOM 元素索引
3. 若运行时突发不可用 - 捕获异常 - 降级文本模型 - 移除截图消息 - 重试 think()

## 更新历史
| 日期 | 版本 | 变更说明 |
|------|------|----------|
| 2026-07-27 | v0.3.0-gui | 首次实现模型自动切换与降级机制 |
