# FR-6: ASR 插件注册 — Gateway 自动语音识别

> TODO: a69ac328 | 关联: nanobot §76 (Gateway ASR 插件注册架构)
> 状态: 需求已对齐，待排期开发

## Summary

feishu-parser skill 作为 ASR 引擎提供方，按 gateway 插件注册规范注册自身，提供 ASR 脚本供 gateway 调用。

## 背景

nanobot §76 在 gateway 侧实现了 ASR 插件注册架构（`~/.nanobot/plugins/asr/` 目录），gateway 启动时扫描注册 JSON 并调用对应脚本。feishu-parser skill 需要：
1. 提供符合接口规范的 ASR 脚本
2. 将注册 JSON 写入插件目录
3. 支持多引擎内部降级（飞书 ASR → 备选引擎），对 gateway 透明

## 方案

1. 新增 `scripts/asr.py` — ASR 入口脚本，接收 file_key + duration，返回 recognition + engine
2. 脚本内部支持多引擎降级（飞书 file_recognize 为主，失败降级到备选），降级逻辑对 gateway 透明
3. 注册文件: skill 初始化/安装时写 `~/.nanobot/plugins/asr/feishu-asr.json`
4. SKILL.md 补充 ASR 脚本接口规范文档

## 注册文件示例

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

## 接口规范

### ASR 脚本调用方式

Gateway 通过 subprocess 调用 ASR 脚本：

```bash
python3 ~/.nanobot/workspace/skills/feishu-parser/scripts/asr.py --file-key <file_key> --duration <duration_ms>
```

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--file-key` | str | ✅ | 飞书音频文件 key |
| `--duration` | int | ✅ | 音频时长（毫秒） |

### 输出格式

脚本将 JSON 输出到 stdout：

**成功**:
```json
{
  "recognition": "识别出的文本内容",
  "engine": "feishu"
}
```

**失败**:
```json
{
  "error": "错误描述",
  "engine": "feishu"
}
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功，stdout 包含 recognition |
| 1 | 失败，stdout 包含 error 信息 |

### 引擎降级逻辑

脚本内部维护引擎优先级列表：

1. **飞书 file_recognize**（主引擎）— 通过飞书开放平台 ASR 接口
2. **备选引擎**（待定）— 主引擎失败时自动切换

降级对 gateway 完全透明，gateway 只关心返回值中的 `recognition` 和 `engine` 字段。

## Glossary

| 术语 | 定义 |
|------|------|
| ASR | Automatic Speech Recognition，语音自动识别 |
| 注册 JSON | 放在 `~/.nanobot/plugins/asr/` 下的 JSON 文件，描述引擎信息和脚本路径 |
| 引擎降级 | ASR 脚本内部支持多个识别引擎，主引擎失败自动切换备选，对调用方透明 |

## 验收 Checklist

- [ ] ✅ `scripts/asr.py` 存在且可独立执行
- [ ] ✅ 注册 JSON 写入 `~/.nanobot/plugins/asr/feishu-asr.json`
- [ ] ✅ 返回值包含 recognition + engine 字段
- [ ] ✅ 引擎降级正常工作（主引擎失败切备选）
- [ ] ✅ SKILL.md 有 ASR 脚本接口规范
- [ ] 👤 配合 §76 联调：飞书发语音 → gateway 加载 plugin → 调脚本 → 返回识别结果
