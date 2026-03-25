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

### FR-6: ASR 插件注册 — Gateway 自动语音识别

> 详情: [requirements/fr6-asr-plugin-registration.md](requirements/fr6-asr-plugin-registration.md)
> TODO: a69ac328 | 关联: nanobot §76 (Gateway ASR 插件注册架构)
> 状态: 需求已对齐，待排期开发

**Summary**: feishu-parser skill 作为 ASR 引擎提供方，按 gateway 插件注册规范注册自身，提供 ASR 脚本供 gateway 调用。

**背景**: nanobot §76 在 gateway 侧实现了 ASR 插件注册架构（`~/.nanobot/plugins/asr/` 目录），gateway 启动时扫描注册 JSON 并调用对应脚本。feishu-parser skill 需要：
1. 提供符合接口规范的 ASR 脚本
2. 将注册 JSON 写入插件目录
3. 支持多引擎内部降级（飞书 ASR → 备选引擎），对 gateway 透明

**方案**:
1. 新增 `scripts/asr.py` — ASR 入口脚本，接收 file_key + duration，返回 recognition + engine
2. 脚本内部支持多引擎降级（飞书 file_recognize 为主，失败降级到备选），降级逻辑对 gateway 透明
3. 注册文件: skill 初始化/安装时写 `~/.nanobot/plugins/asr/feishu-asr.json`
4. SKILL.md 补充 ASR 脚本接口规范文档

**注册文件示例**:
```json
{
  "engine": "feishu",
  "enabled": true,
  "script": "~/.nanobot/workspace/skills/feishu-parser/scripts/asr.py",
  "args_schema": {"file_key": "str", "duration": "int"},
  "output_schema": {"recognition": "str", "engine": "str"},
  "timeout": 30
}
```

**Glossary**:

| 术语 | 定义 |
|------|------|
| ASR | Automatic Speech Recognition，语音自动识别 |
| 注册 JSON | 放在 `~/.nanobot/plugins/asr/` 下的 JSON 文件，描述引擎信息和脚本路径 |
| 引擎降级 | ASR 脚本内部支持多个识别引擎，主引擎失败自动切换备选，对调用方透明 |

**验收 Checklist**:
- [ ] ✅ `scripts/asr.py` 存在且可独立执行
- [ ] ✅ 注册 JSON 写入 `~/.nanobot/plugins/asr/feishu-asr.json`
- [ ] ✅ 返回值包含 recognition + engine 字段
- [ ] ✅ 引擎降级正常工作（主引擎失败切备选）
- [ ] ✅ SKILL.md 有 ASR 脚本接口规范
- [ ] 👤 配合 §76 联调：飞书发语音 → gateway 加载 plugin → 调脚本 → 返回识别结果

## 技术约束

- **依赖**：lark-oapi（Python SDK）、requests（用于 tenant_token 获取和 GET API 调用）
- **鉴权**：从 `~/.nanobot/config.json` 读取飞书应用凭证（appId/appSecret）
- **输出约定**：JSON 输出到 stdout，日志/警告输出到 stderr
- **已知限制**：合并转发子消息中的媒体文件无法通过 API 下载（飞书平台限制，错误码 234003/234043）
