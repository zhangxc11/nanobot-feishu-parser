"""Common Feishu utilities shared across feishu skills.

Provides:
- load_feishu_credentials(app_name) — load app_id/app_secret from config
- create_client(app_name) — create a lark_oapi.Client
- get_tenant_token(app_name) — get tenant_access_token for raw HTTP calls
"""

import json
import os
import sys
from typing import Tuple

try:
    import lark_oapi as lark
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    lark = None


def load_feishu_credentials(app_name: str = "lab") -> Tuple[str, str]:
    """Load Feishu app credentials from nanobot config.

    Args:
        app_name: Name of the Feishu app in config (default: "lab")

    Returns:
        Tuple of (app_id, app_secret)
    """
    config_path = os.path.expanduser("~/.nanobot/config.json")
    if not os.path.exists(config_path):
        print(f"ERROR: Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, 'r') as f:
        config = json.load(f)

    feishu_apps = config.get("channels", {}).get("feishu", [])
    if isinstance(feishu_apps, dict):
        # Legacy single-app format
        feishu_apps = [feishu_apps]
    if not isinstance(feishu_apps, list):
        print("ERROR: channels.feishu should be an array in config.json", file=sys.stderr)
        sys.exit(1)

    target_app = None
    for app in feishu_apps:
        if app.get("name") == app_name:
            target_app = app
            break

    if not target_app:
        available = [a.get("name", "?") for a in feishu_apps]
        print(f"ERROR: Feishu app '{app_name}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)

    app_id = target_app.get("appId", "")
    app_secret = target_app.get("appSecret", "")
    if not app_id or not app_secret:
        print(f"ERROR: appId or appSecret not configured for '{app_name}'", file=sys.stderr)
        sys.exit(1)

    return app_id, app_secret


def create_client(app_name: str = "lab") -> "lark.Client":
    """Create a Feishu API client."""
    if not LARK_AVAILABLE:
        print("ERROR: lark_oapi not installed. Run: pip install lark-oapi", file=sys.stderr)
        sys.exit(1)

    app_id, app_secret = load_feishu_credentials(app_name)
    client = lark.Client.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .log_level(lark.LogLevel.WARNING) \
        .build()
    return client


def get_tenant_token(app_name: str = "lab") -> str:
    """Get tenant_access_token via HTTP for APIs not covered by SDK."""
    import requests
    app_id, app_secret = load_feishu_credentials(app_name)
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        print(f"ERROR: Failed to get tenant token: {data}", file=sys.stderr)
        sys.exit(1)
    return data["tenant_access_token"]
