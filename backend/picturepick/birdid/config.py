"""AI 识鸟配置：环境变量 + 本机 settings.json（docs/BIRDID-CONTRACT.md §2）。

**端点语义（v5.1 起）**：配置项是**完整的 API 端点 URL**，后端直接向它发请求，
不再做任何隐藏的路径拼接。支持两种火山方舟接口，按端点路径自动选择协议：

* Chat Completions：``https://ark.cn-beijing.volces.com/api/v3/chat/completions``（默认）
* Responses API：``https://ark.cn-beijing.volces.com/api/v3/responses``

解析优先级（高 → 低）：

* API Key：``PICTUREPICK_ARK_API_KEY`` → ``ARK_API_KEY`` → 配置文件 ``arkApiKey``；
* 端点：``PICTUREPICK_ARK_ENDPOINT`` → （兼容旧变量 ``PICTUREPICK_ARK_BASE_URL``）
  → 配置文件 ``arkEndpoint`` → （兼容旧字段 ``arkBaseUrl``）→ 默认 Chat Completions 端点；
* 模型：``PICTUREPICK_ARK_MODEL`` → 配置文件 ``arkModel`` → ``doubao-seed-2-1-lite``；
* 模拟模式：``PICTUREPICK_BIRD_MOCK`` → 配置文件 ``mock``。

配置文件默认位于 ``%APPDATA%/PicturePick/settings.json``（其它平台
``~/.config/PicturePick/settings.json``），可用 ``PICTUREPICK_CONFIG_DIR`` 覆盖目录
（测试用它避免污染真实用户配置）。Key 只保存在后端进程内，绝不写日志/响应。
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ..errors import INVALID_CONFIG, ApiError

#: 默认端点：Chat Completions（完整 URL，后端原样使用）
DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-1-lite"

#: 支持的端点后缀 → 协议
ENDPOINT_PROTOCOLS = {
    "/chat/completions": "chat",
    "/responses": "responses",
}

CONFIG_DIR_ENV = "PICTUREPICK_CONFIG_DIR"
CONFIG_FILENAME = "settings.json"

ENV_API_KEY = "PICTUREPICK_ARK_API_KEY"
ENV_API_KEY_LEGACY = "ARK_API_KEY"
ENV_ENDPOINT = "PICTUREPICK_ARK_ENDPOINT"
#: v5.0 的旧变量名（当时值是 Base URL）；仍可读取，但值必须已经包含端点路径
ENV_ENDPOINT_LEGACY = "PICTUREPICK_ARK_BASE_URL"
ENV_MODEL = "PICTUREPICK_ARK_MODEL"
ENV_MOCK = "PICTUREPICK_BIRD_MOCK"
ENV_MOCK_DELAY = "PICTUREPICK_BIRD_MOCK_DELAY"
ENV_MOCK_FAIL = "PICTUREPICK_BIRD_MOCK_FAIL"

_TRUTHY = {"1", "true", "yes", "on"}


def _env_bool(name: str) -> bool:
    raw = os.environ.get(name)
    return bool(raw) and raw.strip().lower() in _TRUTHY


def _env_bool_optional(name: str) -> Optional[bool]:
    """环境变量未设置/为空 → None；否则按布尔词解析（用于覆盖配置文件）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower() in _TRUTHY


def config_dir() -> Path:
    """配置目录：``PICTUREPICK_CONFIG_DIR`` 优先，否则按平台惯例。"""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override and override.strip():
        return Path(override.strip()).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "PicturePick"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and xdg.strip():
        return Path(xdg.strip()).expanduser() / "PicturePick"
    return Path.home() / ".config" / "PicturePick"


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_settings() -> Dict[str, Any]:
    """读取配置文件；缺失/损坏/非对象 → 返回空 dict（不阻塞启动）。"""
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _clean_string(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def endpoint_protocol(endpoint: str) -> Optional[str]:
    """按端点路径判断协议；不拼接、不猜测，只认已支持的两种完整端点。

    形如 ``/api/v3/responses/chat/completions`` 的「双重端点」不算有效端点。
    """
    try:
        path = urllib.parse.urlsplit(str(endpoint or "").strip()).path
    except ValueError:
        return None
    normalized = path.rstrip("/").lower()
    matched = [(suffix, proto) for suffix, proto in ENDPOINT_PROTOCOLS.items() if normalized.endswith(suffix)]
    if len(matched) != 1:
        return None
    suffix, protocol = matched[0]
    prefix = normalized[: -len(suffix)]
    if any(other in prefix for other in ENDPOINT_PROTOCOLS):
        return None
    return protocol


def endpoint_hint() -> str:
    return (
        "请填写完整接口 URL（二选一）："
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions（Chat Completions）"
        " 或 https://ark.cn-beijing.volces.com/api/v3/responses（Responses API）"
    )


def validate_endpoint(value: Any) -> str:
    """校验并规范化端点 URL（只去首尾空白/末尾斜杠，不改路径、不拼接）。"""
    url = str(value or "").strip().rstrip("/")
    if not url:
        raise ApiError(INVALID_CONFIG, "API 端点不能为空。" + endpoint_hint())
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError(INVALID_CONFIG, "API 端点必须以 http:// 或 https:// 开头")
    if endpoint_protocol(url) is None:
        raise ApiError(
            INVALID_CONFIG,
            f"API 端点不是受支持的完整接口 URL：{url}。" + endpoint_hint(),
        )
    return url


def save_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """把 updates 合并进配置文件（None = 删除该字段），原子写入。"""
    current = load_settings()
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    target = config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _tighten_permissions(target.parent, is_dir=True)
    except OSError as exc:
        raise ApiError(INVALID_CONFIG, f"无法创建配置目录: {target.parent}（{exc}）") from exc

    descriptor = -1
    temp_path = ""
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".picturepick-settings-", suffix=".json", dir=str(target.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(current, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, target)
        temp_path = ""
        _tighten_permissions(target, is_dir=False)
    except OSError as exc:
        raise ApiError(INVALID_CONFIG, f"无法写入配置文件: {target}（{exc}）") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - 清理尽力而为
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:  # pragma: no cover - 清理尽力而为
                pass
    return current


def _tighten_permissions(path: Path, is_dir: bool) -> None:
    """尽力收紧权限（Windows 上 chmod 语义有限，失败忽略）。"""
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)
    except OSError:  # pragma: no cover - 平台差异
        pass


@dataclass(frozen=True)
class BirdConfig:
    #: API Key 不参与 repr，避免日志/异常信息泄露
    api_key: str = field(repr=False)
    #: 完整 API 端点 URL（后端原样 POST，不做路径拼接）
    endpoint: str
    model: str
    mock: bool
    source: str  #: key 来源：env / config / mock / ""
    mock_delay: float = 0.6
    mock_fail: bool = False
    #: 端点不可用/旧配置时的提示（不阻断读取，提交任务时会返回 400）
    endpoint_warning: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self.mock or bool(self.api_key)

    @property
    def protocol(self) -> Optional[str]:
        """``chat`` / ``responses`` / None（不支持的端点）。"""
        return endpoint_protocol(self.endpoint)

    @property
    def endpoint_supported(self) -> bool:
        return self.protocol is not None

    def masked_key(self) -> Optional[str]:
        return mask_key(self.api_key)


def mask_key(key: str) -> Optional[str]:
    """``ark-1234567890abcd`` → ``ark-…abcd``；未知格式保留首尾。"""
    text = (key or "").strip()
    if not text:
        return None
    if len(text) <= 8:
        return "…" + text[-2:]
    head = text[:4]
    tail = text[-4:]
    return f"{head}…{tail}"


def _mock_delay() -> float:
    raw = os.environ.get(ENV_MOCK_DELAY)
    if raw is None or not str(raw).strip():
        return 0.6
    try:
        value = float(str(raw).strip())
    except ValueError:
        return 0.6
    return max(0.0, min(5.0, value))


def _endpoint_warning(endpoint: str) -> Optional[str]:
    if endpoint_protocol(endpoint) is not None:
        return None
    return (
        f"当前 API 端点不是受支持的完整接口 URL（{endpoint}）。" + endpoint_hint()
    )


def resolve_config(settings: Optional[Dict[str, Any]] = None) -> BirdConfig:
    """按契约优先级解析当前配置（端点只做首尾空白/末尾斜杠处理，不拼接路径）。"""
    data = load_settings() if settings is None else settings
    env_key = _clean_string(os.environ.get(ENV_API_KEY)) or _clean_string(
        os.environ.get(ENV_API_KEY_LEGACY)
    )
    file_key = _clean_string(data.get("arkApiKey"))
    key = env_key or file_key or ""
    if env_key:
        source = "env"
    elif file_key:
        source = "config"
    else:
        source = ""

    raw_endpoint = (
        _clean_string(os.environ.get(ENV_ENDPOINT))
        or _clean_string(os.environ.get(ENV_ENDPOINT_LEGACY))
        or _clean_string(data.get("arkEndpoint"))
        or _clean_string(data.get("arkBaseUrl"))
        or DEFAULT_ENDPOINT
    )
    endpoint = str(raw_endpoint).strip().rstrip("/") or DEFAULT_ENDPOINT
    if not endpoint.lower().startswith(("http://", "https://")):
        endpoint = DEFAULT_ENDPOINT

    model = (
        _clean_string(os.environ.get(ENV_MODEL))
        or _clean_string(data.get("arkModel"))
        or DEFAULT_MODEL
    )
    mock_env = _env_bool_optional(ENV_MOCK)
    mock = mock_env if mock_env is not None else bool(data.get("mock"))
    if mock and not key:
        source = "mock"
    return BirdConfig(
        api_key=key,
        endpoint=endpoint,
        model=model,
        mock=mock,
        source=source,
        mock_delay=_mock_delay(),
        mock_fail=_env_bool(ENV_MOCK_FAIL),
        endpoint_warning=_endpoint_warning(endpoint),
    )


def update_config(payload: Dict[str, Any]) -> BirdConfig:
    """应用 PUT /api/bird/config 的字段（None/空字符串 = 清除）。"""
    updates: Dict[str, Any] = {}
    if "apiKey" in payload:
        updates["arkApiKey"] = _clean_string(payload.get("apiKey"))
    if "endpoint" in payload:
        raw = _clean_string(payload.get("endpoint"))
        if raw:
            updates["arkEndpoint"] = validate_endpoint(raw)
        else:
            updates["arkEndpoint"] = None  # 清除后回退默认端点
        # 旧字段不再写入，顺便清掉，避免双份配置混淆
        updates["arkBaseUrl"] = None
    if "model" in payload:
        updates["arkModel"] = _clean_string(payload.get("model"))
    if "mock" in payload:
        value = payload.get("mock")
        if value is None:
            updates["mock"] = None
        elif isinstance(value, bool):
            updates["mock"] = value
        else:
            raise ApiError(INVALID_CONFIG, "mock 必须是布尔值")
    if updates:
        save_settings(updates)
    return resolve_config()
