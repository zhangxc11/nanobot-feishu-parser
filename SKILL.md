---
name: feishu-parser
description: "飞书消息解析：获取消息详情、解析合并转发消息、下载媒体文件。支持 dump 原始数据用于调试。当需要解析飞书转发消息、获取消息内容、下载飞书图片/文件时使用。"
---

# 飞书消息解析 Skill

通过飞书开放平台 API 解析消息内容。支持获取单条消息详情、解析合并转发消息、下载媒体文件。

## 脚本位置

```
skills/feishu-parser/scripts/feishu_parser.py
```

## 命令

### 获取单条消息详情

```bash
python3 skills/feishu-parser/scripts/feishu_parser.py get-message --message-id om_xxx [--app lab] [--dump] [--raw]
```

- `--message-id`: 消息 ID（om_ 开头）
- `--app`: 飞书应用名（默认 lab，可选 ST）
- `--dump`: 将原始 API 响应 dump 到 `~/.nanobot/workspace/feishu-dumps/` 目录
- `--raw`: 在输出中包含原始 content 字符串和完整 API 响应

### 解析合并转发消息

```bash
# 方式 1: 通过消息 ID（自动获取 merge_forward 消息内容后解析子消息）
python3 skills/feishu-parser/scripts/feishu_parser.py parse-forward --message-id om_xxx [--app lab] [--dump] [--download]

# 方式 2: 直接提供 content JSON
python3 skills/feishu-parser/scripts/feishu_parser.py parse-forward --content-json '{"message_id_list":["om_a","om_b"]}' [--app lab] [--dump] [--download]
```

- `--download`: 下载子消息中的媒体文件（图片/文件/音频）
- `--dump`: 将原始数据（父消息 + 所有子消息详情）dump 到文件

### 下载媒体文件

```bash
python3 skills/feishu-parser/scripts/feishu_parser.py download-media --message-id om_xxx --key img_xxx --type image [--app lab]
```

- `--type`: 资源类型（image/file/audio/media）
- `--key`: 图片 key（img_xxx）或文件 key

## Dump 文件位置

所有 dump 文件保存在：`~/.nanobot/workspace/feishu-dumps/`

文件命名格式：`{label}_{timestamp}.json`

## 输出格式

所有命令输出 JSON 到 stdout，错误/警告输出到 stderr。

### get-message 输出示例

```json
{
  "message_id": "om_xxx",
  "msg_type": "text",
  "sender_id": "ou_xxx",
  "create_time": "1709312640000",
  "content": {"text": "Hello"}
}
```

### parse-forward 输出示例

```json
{
  "text": "--- forwarded messages ---\nHello\nWorld\n--- end forwarded messages ---",
  "media_paths": ["/path/to/downloaded/image.jpg"]
}
```

## 已知限制

### ⚠️ 合并转发消息中的文件/图片无法下载

合并转发（merge_forward）消息的子消息中包含的文件、图片、音频等资源**无法通过 API 下载**。

- **现象**：`parse-forward --download` 时，文本内容能正常解析，但所有媒体文件下载失败
- **错误码**：`234003 File not in msg`（用子消息 message_id）或 `234043 Unsupported message type`（用父消息 message_id）
- **原因**：飞书合并转发时对资源进行了重新封装，原始 file_key/image_key 在转发后失效，且开放平台 API 不支持从合并转发的子消息中提取资源
- **Workaround**：需要访问文件内容时，请用户从飞书客户端**逐条转发**（而非合并转发）相关消息，或手动下载后单独发送

## 鉴权

自动从 `~/.nanobot/config.json` 读取飞书应用凭证（appId/appSecret），与 feishu-docs skill 共享同一配置。

## 权限要求

飞书应用需开通以下权限：
- `im:message` — 获取与发送单聊、群组消息
- `im:message:readonly` — 读取消息
- `im:message.group_msg` — 获取群组消息
- `im:message.p2p_msg:readonly` — 读取用户发给机器人的单聊消息

## 依赖

- `lark-oapi` (已安装)
- `requests` (已安装)
