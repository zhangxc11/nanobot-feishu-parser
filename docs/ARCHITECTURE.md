# feishu-parser 架构文档

## 整体架构

```
CLI 入口 (argparse)
  ├── get-message    → cmd_get_message()    → get_message_detail()
  ├── parse-forward  → cmd_parse_forward()  → resolve_merge_forward()
  └── download-media → cmd_download_media() → download_media() + save_media_file()
```

所有命令通过 argparse 子命令分发，JSON 输出到 stdout，日志到 stderr。

## 模块说明

### feishu_common.py — 公共工具模块

与 feishu-messenger skill 共享同一设计，提供三个核心函数：

| 函数 | 作用 |
|------|------|
| `load_feishu_credentials(app_name)` | 从 `~/.nanobot/config.json` 加载 appId/appSecret |
| `create_client(app_name)` | 创建 `lark_oapi.Client` 实例 |
| `get_tenant_token(app_name)` | 获取 tenant_access_token（用于 SDK 未覆盖的 HTTP 调用） |

### feishu_parser.py — CLI 主程序（~450 行）

包含所有解析逻辑和 CLI 命令。

## 核心函数

### get_message_detail(client, message_id)

通过 lark-oapi SDK 调用 `GET /im/v1/messages/{message_id}`。

- 返回标准化字典：msg_type / content / content_raw / sender_id / create_time / message_id / _raw
- `_raw` 包含完整 API 响应，用于 dump

### resolve_merge_forward(client, message_id, content_json, do_download, dump)

合并转发消息解析，核心逻辑：

```
resolve_merge_forward()
  ├── 策略1: _get_sub_messages_via_get_api(message_id)
  │     └── GET /im/v1/messages/{id} → 过滤 upper_message_id == id 的子消息
  ├── 策略2 (回退): content_json.message_id_list → 逐条 get_message_detail()
  │
  └── 遍历子消息:
        ├── text       → 直接提取
        ├── post       → extract_post_content()
        ├── image/file → download_media() (if --download)
        ├── share_*    → extract_share_card_content()
        ├── interactive→ extract_interactive_content()
        └── merge_forward → 递归 resolve_merge_forward() (一层)
```

**关键设计：**
- `_get_sub_messages_via_get_api()` 使用 requests 直接调用 REST API（而非 SDK），因为 SDK 的 GET message 响应只返回单条
- GET API 返回 parent + children，通过 `upper_message_id` 字段过滤出子消息
- 嵌套 merge_forward 支持一层递归，避免无限递归

### extract_post_content(content_json)

富文本消息提取：

- 支持多语言 key：zh_cn / en_us / ja_jp / content
- 遍历 paragraph → element，按 tag 分类处理
- text → 直接拼接，a → `[text](href)`，at → `@name`，img → 收集 image_key，emotion → `[emoji_type]`
- 返回 `(text, image_keys)` 元组

### extract_interactive_content(content)

卡片消息提取：

- header.title.content → 加粗标题
- elements 遍历，按 tag 分发到 `extract_element_content()`
- 支持 div（text + fields）、markdown、hr、note、action（button）、column_set（递归）

### download_media(client, message_id, file_key, resource_type)

通过 SDK 的 `GetMessageResourceRequest` 下载媒体资源。返回 `(bytes, filename)`。

### save_media_file(data, filename, subdir)

保存到 `uploads/{date}/` 目录，自动创建目录结构。

## 关键设计决策

### 1. GET API 子消息获取

飞书的 `GET /im/v1/messages/{id}` 对 merge_forward 消息会返回所有 items（parent + children）。子消息通过 `upper_message_id` 字段关联到父消息。这是最可靠的获取方式，因为 merge_forward 的 content 字段不包含 `message_id_list`。

### 2. 双策略回退

保留 content_json 解析作为回退策略，兼容可能存在的旧格式或特殊场景。

### 3. 嵌套递归限制

嵌套 merge_forward 只递归一层（递归调用时 dump=False），避免深层嵌套导致的性能问题和 API 限流。

### 4. dump 调试机制

所有原始 API 响应可通过 `--dump` 保存为 JSON 文件，便于：
- 分析 API 响应结构变化
- 复现和调试解析问题
- 作为测试数据积累

### 5. stdout/stderr 分离

JSON 结果输出到 stdout，日志/警告/错误输出到 stderr。确保管道和自动化场景下输出可靠解析。

## 文件存储

| 目录 | 用途 |
|------|------|
| `~/.nanobot/workspace/uploads/{date}/` | 下载的媒体文件 |
| `~/.nanobot/workspace/feishu-dumps/` | dump 的原始 API 响应 |
