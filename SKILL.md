---
name: feishu-parser
description: "飞书消息解析与语音识别(ASR/transcribe)：获取消息详情、解析合并转发消息、下载媒体文件、语音转文字。支持 dump 原始数据用于调试。当需要解析飞书转发消息、获取消息内容、下载飞书图片/文件，或收到语音消息 [audio: xxx] 需要识别内容时使用。收到语音消息时务必先读取此 SKILL.md。"
---

# 飞书消息解析 Skill

通过飞书开放平台 API 解析消息内容。支持获取单条消息详情、解析合并转发消息、下载媒体文件。

## 飞书语音消息处理流程

当飞书用户发送语音消息时，nanobot 会：
1. 自动下载音频文件（Opus 格式）到 `~/.nanobot/workspace/uploads/YYYY-MM-DD/` 目录
2. 在消息中显示为 `[audio: 文件名, 时长s]`，音频文件路径在附件列表中

**Agent 收到语音消息后的处理规范：**

1. **静默识别** — 调用 transcribe 识别语音，过程中不发送任何中间消息
2. **复述内容** — 将识别结果复述/总结发送给用户
3. **按内容行动** — 根据语音内容继续后续处理

```bash
# 直接对 uploads 中的音频文件调用 transcribe（自动处理格式转换）
python3 skills/feishu-parser/scripts/feishu_parser.py --app ST transcribe ~/.nanobot/workspace/uploads/YYYY-MM-DD/文件名 --engine feishu
```

注意：
- `--app` 参数要和当前飞书 channel 对应（ST 或 lab）
- 音频文件路径从消息附件中获取，无需手动查找
- transcribe 会自动将 Opus 转为 PCM 16kHz 再调用飞书 ASR
- ⚠️ 识别过程中不要发送"正在识别..."等中间状态消息，直接输出结果即可

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

### 语音转文字 (ASR)

```bash
python3 skills/feishu-parser/scripts/feishu_parser.py transcribe <audio_file_path> [--app lab] [--engine auto|feishu|local] [--language zh-CN]
```

- `<audio_file_path>`: 音频文件路径（支持 opus、wav、m4a 等格式）
- `--engine`: ASR 引擎选择（默认 `auto`）
  - `auto`: 先尝试飞书 ASR API，失败后 fallback 到 macOS 本地识别
  - `feishu`: 仅使用飞书 ASR API（`file_recognize` 端点，≤60s 音频）
  - `local`: 仅使用 macOS SFSpeechRecognizer（离线/本地识别）
- `--language`: 语言/locale 代码（默认 `zh-CN`）。**注意：飞书引擎固定使用 `16k_auto`（中英混合自动检测），`--language` 参数仅对 macOS 本地引擎生效**
- 输出 JSON：`{"text": "识别文本", "engine": "feishu|local", "duration_ms": 1234}`

#### ASR 引擎说明

| 引擎 | 优点 | 限制 |
|------|------|------|
| 飞书 ASR | 识别准确率高，中英混合自动检测，有标点符号 | 需要飞书应用开通 `speech_to_text` 权限；音频 ≤60s |
| macOS 本地 | 无需网络 API 权限，速度快约 4 倍（~800ms vs ~3900ms） | 见下方已知局限 |

#### macOS 本地 ASR 已知局限

- **无标点符号**：输出纯文本，无逗号/句号/问号等
- **英文/技术术语识别差**：中文语境中的英文词会被音译为中文谐音（如 feature→"飞车"、pass→"怕死"、skill→"sell"）
- **部分音频 isFinal 返回空文本**：macOS SFSpeechRecognizer 的已知 bug——中间回调有正确文本，但 `isFinal=True` 的最终回调返回空字符串。已通过追踪最佳中间结果作为 fallback 修复
- **适合场景**：作为飞书 ASR 失败时的降级方案（`--engine auto` 模式），或无飞书权限时的备选

#### 飞书 ASR 参数要求

- **端点**: `file_recognize`（非 stream_recognize）
- **file_id**: 恰好 16 位字符（使用 uuid4 hex 截取）
- **format**: 仅支持 `pcm`
- **engine_type**: 仅支持 `16k_auto`（中英混合自动检测）
- **音频格式**: 16kHz 单声道 PCM（脚本自动通过 `afconvert` 转换）

#### 音频格式转换

- 使用 macOS 内置 `afconvert` 工具，无需安装 ffmpeg
- 飞书 ASR：自动转换为 PCM 16kHz 单声道
- macOS 本地：自动转换为 WAV 格式

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
- `speech_to_text:speech` — 语音识别（transcribe 子命令所需）

## 依赖

- `lark-oapi` (已安装)
- `requests` (已安装)
- `pyobjc-framework-Speech` (已安装，macOS 本地 ASR fallback 所需)
