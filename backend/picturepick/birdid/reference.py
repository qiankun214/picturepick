"""参考照片：由大模型 API 提供真实图片直链，后端下载到内存（docs/BIRDID-CONTRACT.md §4.4）。

* 参考照片的 URL（``referenceImageUrl``）由大模型在识别结果 JSON 中给出；模型在
  Responses API 下可结合方舟「联网搜索」工具检索真实来源页面，再填图片直链。
  后端**不做图片搜索/生成**，只负责安全下载、缩放和代理显示。
* 参考照片只在任务内存中保存 ``bytes``，**不写磁盘**；前端经
  ``GET /api/sessions/{sid}/bird-tasks/{tid}/reference`` 由后端代理，避免 CORS/防盗链。
* 下载前做 SSRF 防护（拒绝 localhost/内网/文件协议），限制大小与超时。
"""

from __future__ import annotations

import io
import ipaddress
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PIL import Image, ImageOps

from .parser import ParsedIdentification

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 PicturePick/1.0"
)
MAX_BYTES = 15 * 1024 * 1024
TIMEOUT_SECONDS = 6
MAX_EDGE = 1000
JPEG_QUALITY = 85

#: 中国大陆通常无法访问的图源（大模型可能凭训练知识给出这些链接，直接跳过）
BLOCKED_REFERENCE_HOSTS = ("wikipedia.org", "wikimedia.org")

#: 注入用的 HTTP GET：返回 (content_type, body)
HttpGetter = Callable[[str], Tuple[str, bytes]]


@dataclass
class ReferenceImage:
    data: bytes
    content_type: str
    source_url: str
    source_label: str


class ReferenceError(Exception):
    """参考照片获取失败（内部使用，不影响识别任务成功）。"""


def default_http_get(url: str, timeout: float = TIMEOUT_SECONDS) -> Tuple[str, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - SSRF 已校验
        status = getattr(response, "status", 200)
        if status >= 400:
            raise ReferenceError(f"HTTP {status}")
        content_type = response.headers.get_content_type() or "application/octet-stream"
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ReferenceError("图片超过 15MB 限制")
    if not body:
        raise ReferenceError("响应内容为空")
    return content_type, body


def _is_safe_public_url(url: str) -> bool:
    """校验外部图片 URL 是否安全可抓取（防 SSRF：拒绝 localhost/内网/文件协议）。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 域名：只做字面校验，不解析 DNS（真实连接由 urllib 完成）
        return True
    return address.is_global


def _is_blocked_reference_host(url: str) -> bool:
    """维基/维基媒体图片在中国大陆通常不可达，视为不可用参考图。"""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return True
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_REFERENCE_HOSTS)


def _download_image(url: str, http_get: HttpGetter) -> Tuple[bytes, str]:
    if not _is_safe_public_url(url):
        raise ReferenceError(f"不安全的参考图片来源: {url[:120]}")
    content_type, body = http_get(url)
    if not str(content_type).lower().startswith("image/"):
        raise ReferenceError(f"参考图不是图片类型: {content_type}")
    return body, str(content_type)


def shrink_image(data: bytes) -> Optional[Tuple[bytes, str]]:
    """把参考图缩到长边 <= 1000 的 JPEG（保持宽高比）；失败返回 None。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            longest = max(width, height)
            if longest > MAX_EDGE:
                scale = MAX_EDGE / float(longest)
                size = (max(1, int(width * scale)), max(1, int(height * scale)))
                resample = getattr(Image, "Resampling", Image).LANCZOS
                image = image.resize(size, resample)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - 无法解码的图片直接放弃参考图
        return None


def resolve_reference(
    parsed: ParsedIdentification,
    http_get: Optional[HttpGetter] = None,
    timeout: float = TIMEOUT_SECONDS,
) -> Optional[ReferenceImage]:
    """使用大模型给出的真实参考图直链；缺少/不可访问时返回 None。

    这里**不做**任何图片搜索或生成：参考图必须来自模型 API 的识别结果
    （``referenceImageUrl``），符合「参考照片由大模型 API 提供」的要求。
    """
    if not parsed.result.identified:
        return None
    url = (parsed.reference_image_url or "").strip()
    if not url:
        return None
    if _is_blocked_reference_host(url):
        return None
    if not _is_safe_public_url(url):
        return None
    if http_get is not None:
        getter: HttpGetter = http_get
    else:
        getter = lambda target: default_http_get(target, timeout=timeout)  # noqa: E731
    try:
        body, _content_type = _download_image(url, getter)
    except Exception:  # noqa: BLE001 - 参考图失败不影响识别任务
        return None
    shrunk = shrink_image(body)
    if shrunk is None:
        return None
    data, content_type = shrunk
    label = (parsed.result.referenceSource or "").strip() or "大模型提供的参考图"
    return ReferenceImage(
        data=data,
        content_type=content_type,
        source_url=url,
        source_label=label,
    )
