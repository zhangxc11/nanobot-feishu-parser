# feishu-parser 开发日志

## Phase 1: 初始实现 ✅

> 来源：nanobot core Phase 17（飞书合并转发消息解析）+ Phase 22（飞书 SDK 操作 Skill 化）

### T1.1: feishu_common.py 公共模块 ✅

从 nanobot gateway 提取公共工具函数：
- `load_feishu_credentials()` — 从 config.json 加载飞书应用凭证
- `create_client()` — 创建 lark-oapi Client
- `get_tenant_token()` — 获取 tenant_access_token

与 feishu-messenger skill 共享同一设计。

### T1.2: get-message 命令 ✅

实现 `get_message_detail()` 函数和 `cmd_get_message()` CLI 命令：
- 通过 SDK 调用 GET /im/v1/messages/{id}
- 返回标准化消息结构
- 支持 --dump 和 --raw 参数

### T1.3: parse-forward 命令（merge_forward 解析）✅

实现 `resolve_merge_forward()` 核心逻辑：
- 策略 1：GET API 获取子消息（upper_message_id 过滤）
- 策略 2：content_json message_id_list 回退
- 嵌套 merge_forward 递归支持（一层）

### T1.4: download-media 命令 ✅

实现媒体下载：
- `download_media()` — 通过 SDK GetMessageResourceRequest 下载
- `save_media_file()` — 保存到 uploads/{date}/ 目录
- 支持 image / file / audio / media 四种类型

### T1.5: 内容提取器 ✅

实现各消息类型的内容提取：
- `extract_post_content()` — 富文本：文本 + 图片 key + 链接 + @ + emoji
- `extract_interactive_content()` + `extract_element_content()` — 卡片消息
- `extract_share_card_content()` — share_chat / share_user / share_calendar_event / system

### T1.6: dump 调试功能 ✅

- `dump_data()` 函数：JSON 序列化保存到 feishu-dumps/
- 所有命令支持 --dump 参数
- 文件命名：{label}_{timestamp}.json

### T1.7: SKILL.md 编写 ✅

编写 skill 说明文档，包含命令用法、输出格式、已知限制、鉴权说明。

---

## Bug Fix: merge_forward 解析修复 (2025-03-02) ✅

**问题现象：** parse-forward 命令无法获取合并转发消息的子消息列表。

**根因分析：** 飞书 merge_forward 消息的 content 字段是纯文本 `"Merged and Forwarded Message"`，不是 JSON，不包含 `message_id_list`。原始实现依赖从 content JSON 中提取子消息 ID 列表，在实际场景中完全失效。

**修复方案：** 改用 `GET /im/v1/messages/{message_id}` REST API 直接获取子消息。该 API 对 merge_forward 消息会返回所有 items（parent + children），子消息通过 `upper_message_id` 字段关联。

**变更：**
- 新增 `_get_sub_messages_via_get_api()` 函数
- `resolve_merge_forward()` 改为双策略：GET API 优先，content_json 回退
- GET API 使用 requests 直接调用（绕过 SDK 单条返回限制）

---

*所有任务已完成。*
