#!/usr/bin/env python3
"""飞书消息解析 CLI — nanobot feishu-parser skill

解析飞书消息内容，支持获取单条消息详情、解析合并转发消息、下载媒体文件、语音转文字。
带 dump 功能，方便调试和迭代。

用法:
  python3 feishu_parser.py get-message --message-id om_xxx [--app lab] [--dump]
  python3 feishu_parser.py parse-forward --message-id om_xxx [--app lab] [--dump] [--download]
  python3 feishu_parser.py parse-forward --content-json '{"message_id_list":[...]}' [--app lab] [--dump] [--download]
  python3 feishu_parser.py download-media --message-id om_xxx --type image --key img_xxx [--app lab]
  python3 feishu_parser.py transcribe <audio_file> [--app lab] [--engine auto|feishu|local] [--language zh-CN]

安全说明:
  - appSecret 仅在此脚本进程内使用，不输出到 stdout
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Add script dir to path for feishu_common
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu_common import create_client, load_feishu_credentials, get_tenant_token, LARK_AVAILABLE

if LARK_AVAILABLE:
    from lark_oapi.api.im.v1 import (
        GetMessageRequest,
        GetMessageResourceRequest,
    )

# ── Constants ─────────────────────────────────────────────────────────

DUMP_DIR = Path.home() / ".nanobot" / "workspace" / "feishu-dumps"
UPLOAD_DIR = Path.home() / ".nanobot" / "workspace" / "uploads"

MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
    "media": "[media]",
}


# ── Message detail fetching ──────────────────────────────────────────

def get_message_detail(client, message_id: str) -> Optional[dict]:
    """Fetch a single message's detail via GET /im/v1/messages/{message_id}.

    Returns a dict with keys: msg_type, content (parsed JSON), content_raw (string),
    sender_id, create_time, message_id, _raw (full API response for dump).
    Returns None on failure.
    """
    try:
        request = GetMessageRequest.builder().message_id(message_id).build()
        response = client.im.v1.message.get(request)

        if not response.success():
            print(f"WARNING: Failed to get message {message_id}: "
                  f"code={response.code}, msg={response.msg}", file=sys.stderr)
            return None

        items = response.data.items if response.data else None
        if not items:
            print(f"WARNING: No items returned for message {message_id}", file=sys.stderr)
            return None

        msg = items[0]
        content_str = msg.body.content if msg.body else ""
        try:
            content_json = json.loads(content_str) if content_str else {}
        except json.JSONDecodeError:
            content_json = {}

        sender_id = ""
        if msg.sender:
            sender_id = msg.sender.id or ""

        # Build raw representation for dump
        raw_data = {
            "message_id": msg.message_id,
            "msg_type": msg.msg_type,
            "create_time": msg.create_time,
            "sender": {
                "id": sender_id,
                "sender_type": getattr(msg.sender, "sender_type", None) if msg.sender else None,
                "tenant_key": getattr(msg.sender, "tenant_key", None) if msg.sender else None,
            },
            "body": {
                "content": content_str,
            },
            "chat_id": getattr(msg, "chat_id", None),
            "upper_message_id": getattr(msg, "upper_message_id", None),
            "parent_id": getattr(msg, "parent_id", None),
            "root_id": getattr(msg, "root_id", None),
        }

        return {
            "msg_type": msg.msg_type or "text",
            "content": content_json,
            "content_raw": content_str,
            "sender_id": sender_id,
            "create_time": msg.create_time,
            "message_id": msg.message_id or message_id,
            "_raw": raw_data,
        }
    except Exception as e:
        print(f"ERROR: Exception fetching message {message_id}: {e}", file=sys.stderr)
        return None


# ── Post content extraction ──────────────────────────────────────────

def extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from a post (rich text) message.

    Returns (text, image_keys).
    """
    text_parts = []
    image_keys = []

    def extract_from_lang(lang_content: dict) -> tuple[Optional[str], list[str]]:
        title = lang_content.get("title")
        imgs = []
        lines = []
        for paragraph in lang_content.get("content", []):
            line_parts = []
            for element in paragraph:
                tag = element.get("tag", "")
                if tag == "text":
                    line_parts.append(element.get("text", ""))
                elif tag == "a":
                    href = element.get("href", "")
                    text = element.get("text", href)
                    line_parts.append(f"[{text}]({href})")
                elif tag == "at":
                    user_name = element.get("user_name", element.get("user_id", "someone"))
                    line_parts.append(f"@{user_name}")
                elif tag == "img":
                    img_key = element.get("image_key", "")
                    if img_key:
                        imgs.append(img_key)
                elif tag == "emotion":
                    emoji_type = element.get("emoji_type", "")
                    line_parts.append(f"[{emoji_type}]")
            if line_parts:
                lines.append("".join(line_parts))
        full_text = "\n".join(lines)
        if title:
            full_text = f"{title}\n{full_text}"
        return full_text if full_text else None, imgs

    # Try different language keys
    for key in ("zh_cn", "en_us", "ja_jp", "content"):
        if key in content_json:
            lang_data = content_json[key]
            # Handle case where content is a list (direct paragraph list) vs dict
            if isinstance(lang_data, dict):
                text, imgs = extract_from_lang(lang_data)
            elif isinstance(lang_data, list):
                # Direct paragraph list without title
                text, imgs = extract_from_lang({"content": lang_data})
            else:
                continue
            if text:
                text_parts.append(text)
            image_keys.extend(imgs)
            break

    return "\n".join(text_parts), image_keys


def extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract readable text from share cards and interactive messages."""
    if msg_type == "share_chat":
        name = content_json.get("chat_name", content_json.get("name", ""))
        return f"[shared group: {name}]" if name else "[shared group]"
    elif msg_type == "share_user":
        user_id = content_json.get("user_id", "")
        return f"[shared user: {user_id}]" if user_id else "[shared user]"
    elif msg_type == "interactive":
        parts = extract_interactive_content(content_json)
        return "\n".join(parts) if parts else "[interactive card]"
    elif msg_type == "share_calendar_event":
        summary = content_json.get("summary", "")
        return f"[shared calendar event: {summary}]" if summary else "[shared calendar event]"
    elif msg_type == "system":
        return content_json.get("content", "[system message]")
    else:
        return f"[{msg_type}]"


def extract_interactive_content(content: dict) -> list[str]:
    """Extract text from interactive card content."""
    parts = []
    # Header
    header = content.get("header", {})
    title = header.get("title", {})
    if isinstance(title, dict):
        t = title.get("content", "")
        if t:
            parts.append(f"**{t}**")

    # Elements
    for element in content.get("elements", []):
        parts.extend(extract_element_content(element))

    return parts


def extract_element_content(element: dict) -> list[str]:
    """Extract text from a single interactive card element."""
    parts = []
    tag = element.get("tag", "")

    if tag == "div":
        text_obj = element.get("text", {})
        if isinstance(text_obj, dict):
            content = text_obj.get("content", "")
            if content:
                parts.append(content)
        fields = element.get("fields", [])
        for field in fields:
            if isinstance(field, dict):
                is_short = field.get("is_short", False)
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    fc = field_text.get("content", "")
                    if fc:
                        parts.append(fc)

    elif tag == "markdown":
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag in ("hr",):
        parts.append("---")

    elif tag == "note":
        note_elements = element.get("elements", [])
        for ne in note_elements:
            if isinstance(ne, dict):
                nc = ne.get("content", "")
                if nc:
                    parts.append(f"_{nc}_")

    elif tag == "action":
        actions = element.get("actions", [])
        for action in actions:
            if isinstance(action, dict):
                text_obj = action.get("text", {})
                if isinstance(text_obj, dict):
                    ac = text_obj.get("content", "")
                    if ac:
                        parts.append(f"[button: {ac}]")

    elif tag == "column_set":
        for col in element.get("columns", []):
            for el in col.get("elements", []):
                parts.extend(extract_element_content(el))

    return parts


# ── Media download ───────────────────────────────────────────────────

def download_media(client, message_id: str, file_key: str,
                   resource_type: str = "image") -> tuple[Optional[bytes], Optional[str]]:
    """Download media (image/file/audio) from a Feishu message.

    Returns (file_data, filename) or (None, None) on failure.
    """
    try:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(resource_type)
            .build()
        )
        response = client.im.v1.message_resource.get(request)
        if response.success():
            file_data = response.file
            if hasattr(file_data, "read"):
                file_data = file_data.read()
            return file_data, response.file_name
        else:
            print(f"WARNING: Failed to download {resource_type} {file_key}: "
                  f"code={response.code}, msg={response.msg}", file=sys.stderr)
            return None, None
    except Exception as e:
        print(f"ERROR: Exception downloading {resource_type} {file_key}: {e}", file=sys.stderr)
        return None, None


def save_media_file(data: bytes, filename: str, subdir: Optional[str] = None) -> str:
    """Save media data to uploads directory. Returns the file path."""
    today = date.today().isoformat()
    media_dir = UPLOAD_DIR / today
    if subdir:
        media_dir = media_dir / subdir
    media_dir.mkdir(parents=True, exist_ok=True)
    file_path = media_dir / filename
    file_path.write_bytes(data)
    return str(file_path)


# ── Audio transcription (ASR) ────────────────────────────────────────

def convert_audio_to_wav(input_path: str, sample_rate: int = None) -> str:
    """Convert audio file to WAV format using macOS afconvert.

    Args:
        input_path: Path to input audio file (opus, m4a, etc.)
        sample_rate: Target sample rate (e.g. 16000). None = keep original.

    Returns:
        Path to temporary WAV file. Caller is responsible for cleanup.

    Raises:
        RuntimeError: If conversion fails.
    """
    # Create temp file for output
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    cmd = ["afconvert", input_path, tmp_path, "-d", "LEI16", "-f", "WAVE"]
    if sample_rate:
        # Override the data format spec with sample rate
        cmd = ["afconvert", input_path, tmp_path,
               "-d", f"LEI16@{sample_rate}", "-f", "WAVE", "-c", "1"]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise RuntimeError(
                f"afconvert failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return tmp_path
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError("afconvert timed out (>30s)")
    except FileNotFoundError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError("afconvert not found — this command requires macOS")


def transcribe_feishu(audio_path: str, app_name: str, language: str) -> Optional[str]:
    """Transcribe audio using Feishu ASR API.

    Converts audio to PCM 16kHz mono, then calls the Feishu speech-to-text API.

    Args:
        audio_path: Path to audio file.
        app_name: Feishu app name for credentials.
        language: Language code (e.g. 'zh-CN').

    Returns:
        Recognized text, or None on failure.
    """
    import requests

    wav_path = None
    try:
        # Convert to WAV 16kHz mono
        wav_path = convert_audio_to_wav(audio_path, sample_rate=16000)

        # Read WAV and skip 44-byte header to get raw PCM data
        with open(wav_path, "rb") as f:
            wav_data = f.read()

        # Standard WAV header is 44 bytes; find "data" chunk for robustness
        pcm_data = _extract_pcm_from_wav(wav_data)
        if not pcm_data:
            print("WARNING: [feishu-asr] Failed to extract PCM data from WAV", file=sys.stderr)
            return None

        # Base64 encode
        speech_b64 = base64.standard_b64encode(pcm_data).decode("ascii")

        # Get tenant token
        token = get_tenant_token(app_name)

        # Call Feishu ASR file_recognize API (for audio ≤60s)
        resp = requests.post(
            "https://open.feishu.cn/open-apis/speech_to_text/v1/speech/file_recognize",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "speech": {
                    "speech": speech_b64,
                },
                "config": {
                    "engine_type": "16k_auto",
                    "format": "pcm",
                    "file_id": uuid.uuid4().hex[:16],
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            print(f"WARNING: [feishu-asr] API error: code={data.get('code')}, "
                  f"msg={data.get('msg')}", file=sys.stderr)
            return None

        recognition_text = data.get("data", {}).get("recognition_text", "")
        return recognition_text

    except requests.RequestException as e:
        print(f"WARNING: [feishu-asr] Request failed: {e}", file=sys.stderr)
        return None
    except SystemExit:
        # get_tenant_token / load_feishu_credentials may sys.exit on bad config;
        # catch here so auto-mode can still fall back to local ASR.
        print("WARNING: [feishu-asr] Feishu credential/token error (SystemExit caught)", file=sys.stderr)
        return None
    except RuntimeError as e:
        print(f"WARNING: [feishu-asr] Audio conversion failed: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"WARNING: [feishu-asr] Unexpected error: {e}", file=sys.stderr)
        return None
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _extract_pcm_from_wav(wav_data: bytes) -> Optional[bytes]:
    """Extract raw PCM data from WAV file bytes.

    Searches for the 'data' chunk and returns its contents.
    Falls back to skipping 44-byte header if chunk not found.
    """
    # Try to find "data" chunk marker
    idx = wav_data.find(b"data")
    if idx >= 0 and idx + 8 <= len(wav_data):
        # 4 bytes "data" + 4 bytes little-endian chunk size
        chunk_size = int.from_bytes(wav_data[idx + 4:idx + 8], byteorder="little")
        pcm_start = idx + 8
        if chunk_size > 0 and pcm_start + chunk_size <= len(wav_data):
            return wav_data[pcm_start:pcm_start + chunk_size]
        # If chunk_size seems wrong, return everything after header
        return wav_data[pcm_start:]

    # Fallback: skip standard 44-byte WAV header
    if len(wav_data) > 44:
        return wav_data[44:]

    return None


def _ensure_speech_authorization() -> bool:
    """Request and wait for SFSpeechRecognizer authorization.

    Returns True if authorized, False otherwise.
    """
    import Speech

    status = Speech.SFSpeechRecognizer.authorizationStatus()
    # 3 = authorized
    if status == 3:
        return True
    # 1 = denied, 2 = restricted
    if status in (1, 2):
        status_names = {1: "denied", 2: "restricted"}
        print(f"WARNING: [local-asr] Speech recognition authorization: {status_names[status]}. "
              f"Grant access in System Settings > Privacy & Security > Speech Recognition.",
              file=sys.stderr)
        return False

    # 0 = notDetermined — request authorization
    auth_event = threading.Event()
    auth_result = [False]

    def auth_handler(granted):
        auth_result[0] = granted
        auth_event.set()

    Speech.SFSpeechRecognizer.requestAuthorization_(auth_handler)
    from Foundation import NSRunLoop, NSDate
    deadline = time.time() + 30
    while not auth_event.is_set() and time.time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))

    if not auth_result[0]:
        print("WARNING: [local-asr] Speech recognition authorization was not granted. "
              "Grant access in System Settings > Privacy & Security > Speech Recognition.",
              file=sys.stderr)
        return False

    return True


def transcribe_local(audio_path: str, language: str) -> Optional[str]:
    """Transcribe audio using macOS SFSpeechRecognizer.

    Converts audio to WAV, then uses Apple's on-device speech recognition.

    Args:
        audio_path: Path to audio file.
        language: Language/locale code (e.g. 'zh-CN').

    Returns:
        Recognized text, or None on failure.
    """
    wav_path = None
    try:
        import Speech  # noqa: N811 — pyobjc framework naming
        import Foundation
    except ImportError:
        print("WARNING: [local-asr] pyobjc-framework-Speech not installed. "
              "Install with: pip install pyobjc-framework-Speech", file=sys.stderr)
        return None

    try:
        # Ensure we have speech recognition authorization
        if not _ensure_speech_authorization():
            return None

        # Convert to WAV (keep original sample rate for better quality)
        wav_path = convert_audio_to_wav(audio_path)

        # Normalize language code: SFSpeechRecognizer expects locale like "zh-CN", "en-US"
        locale_str = language.replace("_", "-")
        locale = Foundation.NSLocale.alloc().initWithLocaleIdentifier_(locale_str)
        recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)

        if not recognizer or not recognizer.isAvailable():
            print(f"WARNING: [local-asr] SFSpeechRecognizer not available for locale '{locale_str}'",
                  file=sys.stderr)
            return None

        url = Foundation.NSURL.fileURLWithPath_(wav_path)
        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)

        # Force on-device recognition if supported
        if recognizer.supportsOnDeviceRecognition():
            request.setRequiresOnDeviceRecognition_(True)

        # Use threading.Event for synchronous wait
        result_text = [None]  # Use list for closure mutability
        best_partial = [None]  # Track best intermediate result as fallback
        error_msg = [None]
        event = threading.Event()

        def handler(result, error):
            if error:
                error_msg[0] = str(error)
                event.set()
                return
            if result:
                text = result.bestTranscription().formattedString()
                if text:
                    best_partial[0] = text
                if result.isFinal():
                    result_text[0] = text
                    event.set()

        recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)

        # Wait up to 120 seconds — pump NSRunLoop so ObjC callbacks fire
        from Foundation import NSRunLoop, NSDate
        deadline = time.time() + 120
        while not event.is_set() and time.time() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))
        if not event.is_set():
            print("WARNING: [local-asr] Recognition timed out (>120s)", file=sys.stderr)
            return None

        if error_msg[0]:
            print(f"WARNING: [local-asr] Recognition error: {error_msg[0]}", file=sys.stderr)
            return None

        # Workaround: macOS SFSpeechRecognizer sometimes returns empty text in
        # the isFinal callback despite producing valid intermediate results.
        # Fall back to the best intermediate transcription in that case.
        final = result_text[0]
        if not final and best_partial[0]:
            print("WARNING: [local-asr] Final result was empty, using best intermediate result",
                  file=sys.stderr)
            return best_partial[0]
        return final

    except RuntimeError as e:
        print(f"WARNING: [local-asr] Audio conversion failed: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"WARNING: [local-asr] Unexpected error: {e}", file=sys.stderr)
        return None
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


# ── Merge forward resolution ────────────────────────────────────────

def _get_sub_messages_via_get_api(client, message_id: str) -> list[dict]:
    """Fetch sub-messages of a merge_forward message using GET /im/v1/messages/{message_id}.

    The GET API returns ALL items: the first is the parent merge_forward message itself,
    and subsequent items (with upper_message_id == message_id) are the sub-messages.

    Returns a list of raw item dicts from the API response.
    """
    import requests as _requests

    # Use feishu_common's get_tenant_token (handles credential loading internally)
    try:
        token = get_tenant_token()
    except Exception as e:
        print(f"ERROR: Failed to get tenant token: {e}", file=sys.stderr)
        return []

    resp = _requests.get(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"WARNING: GET message API failed: code={data.get('code')}, msg={data.get('msg')}", file=sys.stderr)
        return []

    items = data.get("data", {}).get("items", [])
    return items


def resolve_merge_forward(client, message_id: str = None, content_json: dict = None,
                          do_download: bool = False,
                          dump: bool = False) -> tuple[str, list[str], list[dict]]:
    """Resolve a merge_forward message by fetching sub-messages.

    Strategy:
    1. If message_id is provided, use GET API to fetch all sub-messages directly
       (the GET API returns the parent + all children with upper_message_id)
    2. Fallback: try to extract message_id_list from content_json (legacy approach)

    Args:
        client: Feishu API client
        message_id: The message_id of the merge_forward message (preferred)
        content_json: The parsed content JSON (legacy fallback)
        do_download: Whether to download media files
        dump: Whether to collect raw data for dumping

    Returns:
        (text, media_paths, raw_details) where raw_details is populated when dump=True
    """
    raw_details = []
    sub_items = []

    # Strategy 1: Use GET API with message_id to get sub-messages
    if message_id:
        all_items = _get_sub_messages_via_get_api(client, message_id)
        if all_items:
            # Filter: sub-messages have upper_message_id == message_id
            # The first item is usually the parent itself (no upper_message_id or it's None)
            for item in all_items:
                upper = item.get("upper_message_id")
                if upper == message_id:
                    sub_items.append(item)
            print(f"INFO: GET API returned {len(all_items)} items, {len(sub_items)} sub-messages",
                  file=sys.stderr)

    # Strategy 2: Fallback to content_json message_id_list
    if not sub_items and content_json:
        message_ids = content_json.get("message_id_list", [])
        if not message_ids:
            message_ids = content_json.get("messages", [])
            if isinstance(message_ids, list) and message_ids and isinstance(message_ids[0], dict):
                message_ids = [m.get("message_id", "") for m in message_ids if m.get("message_id")]

        if message_ids:
            print(f"INFO: Using content_json message_id_list with {len(message_ids)} IDs", file=sys.stderr)
            for msg_id in message_ids:
                if msg_id:
                    detail = get_message_detail(client, msg_id)
                    if detail:
                        # Convert to raw item format for uniform processing
                        sub_items.append(detail["_raw"])

    if not sub_items:
        print(f"WARNING: No sub-messages found for merge_forward "
              f"(message_id={message_id}, content_json={json.dumps(content_json or {}, ensure_ascii=False)})",
              file=sys.stderr)
        return "[merged forward messages (no sub-messages found)]", [], raw_details

    text_parts = []
    media_paths = []

    for item in sub_items:
        # Parse item (raw API format: body.content is a string, msg_type is at top level)
        sub_msg_id = item.get("message_id", "")
        sub_type = item.get("msg_type", "text")
        content_str = item.get("body", {}).get("content", "") if isinstance(item.get("body"), dict) else ""

        try:
            sub_content = json.loads(content_str) if content_str else {}
        except json.JSONDecodeError:
            sub_content = {}

        if dump:
            raw_details.append(item)

        if sub_type == "text":
            text = sub_content.get("text", "")
            if text:
                text_parts.append(text)

        elif sub_type == "post":
            text, image_keys = extract_post_content(sub_content)
            if text:
                text_parts.append(text)
            if do_download:
                for img_key in image_keys:
                    data, fname = download_media(client, sub_msg_id, img_key, "image")
                    if data:
                        if not fname:
                            fname = f"{img_key[:16]}.jpg"
                        path = save_media_file(data, fname)
                        media_paths.append(path)

        elif sub_type in ("image", "audio", "file", "media"):
            key_field = "image_key" if sub_type == "image" else "file_key"
            file_key = sub_content.get(key_field)
            file_name = sub_content.get("file_name", "")
            if do_download and file_key:
                resource_type = sub_type if sub_type != "image" else "image"
                data, fname = download_media(client, sub_msg_id, file_key, resource_type)
                if data:
                    if not fname:
                        if file_name:
                            fname = file_name
                        else:
                            ext = {"audio": ".opus", "media": ".mp4", "image": ".jpg"}.get(sub_type, "")
                            fname = f"{file_key[:16]}{ext}"
                    path = save_media_file(data, fname)
                    media_paths.append(path)
                    text_parts.append(f"[{sub_type}: {fname}]")
                else:
                    text_parts.append(f"[{sub_type}: download failed]")
            else:
                display_name = file_name or MSG_TYPE_MAP.get(sub_type, f"[{sub_type}]")
                text_parts.append(f"[{sub_type}: {display_name}]" if file_name else display_name)

        elif sub_type in ("share_chat", "share_user", "interactive",
                          "share_calendar_event", "system"):
            text = extract_share_card_content(sub_content, sub_type)
            if text:
                text_parts.append(text)

        elif sub_type == "merge_forward":
            # Nested merge_forward — try to resolve recursively (one level)
            nested_msg_id = item.get("message_id")
            if nested_msg_id:
                nested_text, nested_media, _ = resolve_merge_forward(
                    client, message_id=nested_msg_id, do_download=do_download, dump=False
                )
                text_parts.append(nested_text)
                media_paths.extend(nested_media)
            else:
                text_parts.append("[nested merged forward messages]")

        else:
            display = MSG_TYPE_MAP.get(sub_type, f"[{sub_type}]")
            text_parts.append(display)

    if not text_parts and not media_paths:
        return "[merged forward messages (empty)]", [], raw_details

    header = "--- forwarded messages ---"
    footer = "--- end forwarded messages ---"
    body = "\n".join(text_parts)
    return f"{header}\n{body}\n{footer}", media_paths, raw_details


# ── Dump utility ─────────────────────────────────────────────────────

def dump_data(data: Any, label: str = "dump") -> str:
    """Dump data to a JSON file in DUMP_DIR. Returns the file path."""
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{label}_{timestamp}.json"
    file_path = DUMP_DIR / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return str(file_path)


# ── CLI commands ─────────────────────────────────────────────────────

def cmd_get_message(args):
    """Get a single message's detail."""
    client = create_client(args.app)
    detail = get_message_detail(client, args.message_id)

    if detail is None:
        print(f"ERROR: Failed to fetch message {args.message_id}", file=sys.stderr)
        sys.exit(1)

    if args.dump:
        dump_path = dump_data(detail["_raw"], f"msg_{args.message_id[:16]}")
        print(f"Raw data dumped to: {dump_path}", file=sys.stderr)

    # Output
    output = {
        "message_id": detail["message_id"],
        "msg_type": detail["msg_type"],
        "sender_id": detail["sender_id"],
        "create_time": detail["create_time"],
        "content": detail["content"],
    }
    if args.raw:
        output["content_raw"] = detail["content_raw"]
        output["_raw"] = detail["_raw"]

    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_parse_forward(args):
    """Parse a merge_forward message."""
    client = create_client(args.app)

    content_json = None
    forward_message_id = args.message_id

    if args.content_json:
        # Parse from provided content JSON (legacy mode)
        try:
            content_json = json.loads(args.content_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

    if not forward_message_id and not content_json:
        print("ERROR: Must provide --message-id or --content-json", file=sys.stderr)
        sys.exit(1)

    text, media_paths, raw_details = resolve_merge_forward(
        client,
        message_id=forward_message_id,
        content_json=content_json,
        do_download=args.download,
        dump=args.dump,
    )

    if args.dump and raw_details:
        dump_path = dump_data({
            "parent_message_id": forward_message_id,
            "parent_content": content_json,
            "sub_messages": raw_details,
        }, f"forward_details_{datetime.now().strftime('%H%M%S')}")
        print(f"Sub-message details dumped to: {dump_path}", file=sys.stderr)

    # Output
    output = {
        "text": text,
        "media_paths": media_paths,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_download_media(args):
    """Download a media file from a message."""
    client = create_client(args.app)
    data, filename = download_media(client, args.message_id, args.key, args.type)

    if data is None:
        print(f"ERROR: Failed to download {args.type} {args.key}", file=sys.stderr)
        sys.exit(1)

    if not filename:
        ext = {"audio": ".opus", "media": ".mp4", "image": ".jpg", "file": ""}.get(args.type, "")
        filename = f"{args.key[:16]}{ext}"

    path = save_media_file(data, filename)
    print(json.dumps({"path": path, "filename": filename, "size": len(data)}, indent=2))


def cmd_transcribe(args):
    """Transcribe an audio file to text using ASR."""
    audio_path = args.audio_file

    # Validate input file
    if not os.path.isfile(audio_path):
        print(f"ERROR: Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    engine = args.engine
    language = args.language
    app_name = args.app

    start_time = time.time()
    text = None
    used_engine = None

    if engine in ("auto", "feishu"):
        print(f"INFO: Trying Feishu ASR (language={language})...", file=sys.stderr)
        text = transcribe_feishu(audio_path, app_name, language)
        if text is not None:
            used_engine = "feishu"
        elif engine == "feishu":
            # Feishu-only mode, no fallback
            print("ERROR: Feishu ASR failed and no fallback allowed (--engine feishu)", file=sys.stderr)
            sys.exit(1)
        else:
            print("INFO: Feishu ASR failed, falling back to local macOS ASR...", file=sys.stderr)

    if text is None and engine in ("auto", "local"):
        print(f"INFO: Trying local macOS ASR (language={language})...", file=sys.stderr)
        text = transcribe_local(audio_path, language)
        if text is not None:
            used_engine = "local"
        else:
            print("ERROR: Local macOS ASR also failed", file=sys.stderr)
            sys.exit(1)

    if text is None:
        print("ERROR: All ASR engines failed", file=sys.stderr)
        sys.exit(1)

    duration_ms = int((time.time() - start_time) * 1000)

    if not text:
        print("WARNING: Recognition returned empty text", file=sys.stderr)

    output = {
        "text": text or "",
        "engine": used_engine,
        "duration_ms": duration_ms,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Feishu message parser")
    parser.add_argument("--app", default="lab", help="Feishu app name in config (default: lab)")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # get-message
    p_get = subparsers.add_parser("get-message", help="Get a single message detail")
    p_get.add_argument("--message-id", required=True, help="Message ID (om_xxx)")
    p_get.add_argument("--dump", action="store_true", help="Dump raw API response to file")
    p_get.add_argument("--raw", action="store_true", help="Include raw content in output")

    # parse-forward
    p_fwd = subparsers.add_parser("parse-forward", help="Parse a merge_forward message")
    p_fwd.add_argument("--message-id", help="Message ID of the merge_forward message")
    p_fwd.add_argument("--content-json", help="Content JSON string (alternative to --message-id)")
    p_fwd.add_argument("--dump", action="store_true", help="Dump raw data to files")
    p_fwd.add_argument("--download", action="store_true", help="Download media files")

    # download-media
    p_dl = subparsers.add_parser("download-media", help="Download media from a message")
    p_dl.add_argument("--message-id", required=True, help="Message ID")
    p_dl.add_argument("--key", required=True, help="Image key or file key")
    p_dl.add_argument("--type", default="image", choices=["image", "file", "audio", "media"],
                      help="Resource type (default: image)")

    # transcribe
    p_asr = subparsers.add_parser("transcribe",
                                  help="Transcribe audio file to text (speech-to-text)")
    p_asr.add_argument("audio_file", help="Path to audio file (opus, wav, m4a, etc.)")
    p_asr.add_argument("--engine", default="auto", choices=["auto", "feishu", "local"],
                       help="ASR engine: auto (feishu→local fallback), feishu, or local (default: auto)")
    p_asr.add_argument("--language", default="zh-CN",
                       help="Language/locale code (default: zh-CN)")

    args = parser.parse_args()

    if args.command == "get-message":
        cmd_get_message(args)
    elif args.command == "parse-forward":
        cmd_parse_forward(args)
    elif args.command == "download-media":
        cmd_download_media(args)
    elif args.command == "transcribe":
        cmd_transcribe(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
