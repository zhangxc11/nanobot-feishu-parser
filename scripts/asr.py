#!/usr/bin/env python3
"""ASR plugin script for nanobot gateway — feishu-parser skill.

Provides ASR (Automatic Speech Recognition) via the nanobot gateway
plugin interface (§76).  Gateway calls this script as a subprocess:

    python3 asr.py --file-path /path/to/audio.opus --duration 5000

Output (stdout, JSON):
    {"recognition": "recognized text", "engine": "feishu"}   # success
    {"error": "description", "engine": "feishu"}              # failure

Exit codes:
    0  — success (recognition field present)
    1  — failure (error field present)

Internal engine fallback:
    1. Feishu ASR (file_recognize API) — primary
    2. macOS local SFSpeechRecognizer  — fallback

Registration:
    python3 asr.py --register
    Writes ~/.nanobot/plugins/asr/feishu-asr.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add script dir to path so we can import feishu_parser helpers
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_parser import transcribe_feishu, transcribe_local  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────

PLUGIN_DIR = Path.home() / ".nanobot" / "plugins" / "asr"
REGISTRATION_FILE = PLUGIN_DIR / "feishu-asr.json"

REGISTRATION = {
    "engine": "feishu",
    "enabled": True,
    "script": str(SCRIPT_DIR / "asr.py"),
    "args_schema": {"file_path": "str", "duration": "int"},
    "output_schema": {"recognition": "str", "engine": "str"},
    "timeout": 30,
}


# ── Registration ─────────────────────────────────────────────────────

def do_register() -> None:
    """Write the plugin registration JSON to the plugins directory."""
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRATION_FILE.write_text(
        json.dumps(REGISTRATION, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Registered ASR plugin: {REGISTRATION_FILE}", file=sys.stderr)
    # Also output to stdout for scripting
    print(json.dumps({"registered": str(REGISTRATION_FILE), "engine": "feishu"}))


# ── Recognition ──────────────────────────────────────────────────────

def do_recognize(file_path: str, duration_ms: int) -> None:
    """Recognize audio and output JSON result to stdout."""
    if not os.path.isfile(file_path):
        _fail(f"Audio file not found: {file_path}")

    app_name = os.environ.get("FEISHU_ASR_APP", "ST")
    language = os.environ.get("FEISHU_ASR_LANGUAGE", "zh-CN")

    start = time.time()
    text = None
    engine_used = None

    # Engine 1: Feishu ASR
    print(f"INFO: [asr] Trying Feishu ASR (app={app_name})...", file=sys.stderr)
    try:
        text = transcribe_feishu(file_path, app_name, language)
        if text:
            engine_used = "feishu"
    except Exception as exc:
        print(f"WARNING: [asr] Feishu ASR error: {exc}", file=sys.stderr)

    # Engine 2: macOS local fallback
    if not text:
        print(f"WARNING: [asr] Feishu ASR returned empty text for {file_path}, falling back to local engine", file=sys.stderr)
        try:
            text = transcribe_local(file_path, language)
            if text:
                engine_used = "local"
        except Exception as exc:
            print(f"WARNING: [asr] Local ASR error: {exc}", file=sys.stderr)

    elapsed_ms = int((time.time() - start) * 1000)

    if text:
        print(f"INFO: [asr] Recognition OK ({engine_used}, {elapsed_ms}ms)", file=sys.stderr)
        print(json.dumps({
            "recognition": text,
            "engine": engine_used,
            "duration_ms": elapsed_ms,
        }, ensure_ascii=False))
        sys.exit(0)
    else:
        _fail("All ASR engines failed")


def _fail(message: str) -> None:
    """Output error JSON and exit with code 1."""
    print(f"ERROR: [asr] {message}", file=sys.stderr)
    print(json.dumps({"error": message, "engine": "feishu"}, ensure_ascii=False))
    sys.exit(1)


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASR plugin for nanobot gateway (feishu-parser)",
    )
    parser.add_argument(
        "--register", action="store_true",
        help="Write registration JSON to ~/.nanobot/plugins/asr/",
    )
    parser.add_argument(
        "--file-path", type=str,
        help="Path to local audio file",
    )
    parser.add_argument(
        "--duration", type=int, default=0,
        help="Audio duration in milliseconds",
    )

    args = parser.parse_args()

    if args.register:
        do_register()
        sys.exit(0)

    if not args.file_path:
        parser.error("--file-path is required (or use --register)")

    do_recognize(args.file_path, args.duration)


if __name__ == "__main__":
    main()
