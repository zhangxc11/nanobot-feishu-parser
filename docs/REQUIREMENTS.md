# feishu-parser 需求文档

## 概述

为 nanobot agent 提供飞书消息解析和媒体下载能力。通过飞书开放平台 API，支持获取单条消息详情、解析合并转发（merge_forward）消息、下载媒体文件。

## 背景

nanobot 通过飞书 webhook 接收消息事件，但 webhook 事件中：
- 合并转发消息的 content 仅为 `"Merged and Forwarded Message"` 纯文本，不含实际子消息内容
- 媒体文件（图片/音频/文件）需要额外 API 调用才能获取

本 skill 从 nanobot gateway 中提取独立，专注于消息解析场景，是 nanobot core Phase 17（飞书合并转发消息解析）和 Phase 22（飞书 SDK 操作 Skill 化）的产物。

## 功能需求

### FR-1: get-message — 获取单条消息详情

通过 `GET /im/v1/messages/{message_id}` 获取消息完整信息。

- 输入：message_id（om_ 开头）
- 输出：msg_type、content（解析后 JSON）、sender_id、create_time
- 支持 `--dump` 保存原始 API 响应
- 支持 `--raw` 在输出中包含原始 content 字符串和完整响应

### FR-2: parse-forward — 解析合并转发消息

解析 merge_forward 类型消息，提取所有子消息内容。

**解析策略：**

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | GET API 子消息 | 通过 `GET /im/v1/messages/{message_id}` 获取所有 items，过滤 `upper_message_id == message_id` 的子消息 |
| 2 | content_json 回退 | 从 content JSON 中提取 `message_id_list`，逐条获取详情（兼容旧逻辑） |

- 支持嵌套 merge_forward 递归解析（一层）
- 支持 `--download` 自动下载子消息中的媒体文件
- 支持 `--message-id` 或 `--content-json` 两种输入方式

### FR-3: download-media — 下载媒体文件

通过 `GET /im/v1/messages/{message_id}/resources/{file_key}` 下载媒体资源。

- 支持类型：image / file / audio / media
- 自动保存到 `uploads/{date}/` 目录
- 输出：文件路径、文件名、文件大小

### FR-4: 内容提取

支持从以下消息类型中提取可读内容：

| 消息类型 | 提取方式 |
|----------|----------|
| text | 直接提取 `text` 字段 |
| post（富文本） | 提取文本 + 图片 key，支持链接、@、emoji |
| image / audio / file / media | 下载并保存文件 |
| share_chat | 提取群名称 |
| share_user | 提取用户 ID |
| interactive（卡片） | 提取 header + elements（div/markdown/note/action/column_set） |
| share_calendar_event | 提取事件摘要 |
| system | 提取系统消息内容 |

### FR-5: dump 调试功能

- 所有命令支持 `--dump` 参数
- 原始 API 响应保存到 `~/.nanobot/workspace/feishu-dumps/`
- 文件命名：`{label}_{timestamp}.json`
- 用于调试 API 响应结构变化和排查解析问题

## 技术约束

- **依赖**：lark-oapi（Python SDK）、requests（用于 tenant_token 获取和 GET API 调用）
- **鉴权**：从 `~/.nanobot/config.json` 读取飞书应用凭证（appId/appSecret）
- **输出约定**：JSON 输出到 stdout，日志/警告输出到 stderr
- **已知限制**：合并转发子消息中的媒体文件无法通过 API 下载（飞书平台限制，错误码 234003/234043）
