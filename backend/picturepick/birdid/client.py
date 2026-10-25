"""方舟（Ark）多模态客户端 + 离线模拟客户端（docs/BIRDID-CONTRACT.md §4）。

* ``ArkBirdClient``：stdlib ``urllib`` 调 OpenAI 兼容的 ``/chat/completions``，
  无新增依赖；超时/429/5xx 重试一次，``thinking`` 参数不被接受时自动去掉重试。
* ``MockBirdClient``：``PICTUREPICK_BIRD_MOCK=1`` 时使用，不访问网络，
  按 photoId 的 sha1 返回确定性结果，供 e2e / 演示 / 无 Key 开发。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

from ..errors import BIRD_API_ERROR, BIRD_API_TIMEOUT, BIRD_PARSE_ERROR
from ..models import BirdAlternative, BirdResult
from .config import BirdConfig, endpoint_hint
from .parser import (
    BirdParseError,
    ParsedIdentification,
    extract_json_object,
    parse_model_output,
)
from .prompt import REFERENCE_RETRY_PROMPT, SYSTEM_PROMPT, USER_PROMPT
from .reference import ReferenceImage

MODEL_MAX_EDGE = 1568
MODEL_JPEG_QUALITY = 88
REQUEST_TIMEOUT = 90.0
RETRY_DELAY = 1.5

#: HTTP 注入类型：(url, headers, payload_json_bytes, timeout) -> (status, body_bytes)
HttpPost = Callable[[str, Dict[str, str], bytes, float], Tuple[int, bytes]]


class BirdClientError(Exception):
    """识别调用失败：``code`` 为契约任务级错误码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def prepare_image_bytes(data: bytes) -> bytes:
    """把照片压缩成适合模型输入的 JPEG（EXIF 转正、长边 ≤ 1568、q88）。"""
    resample = getattr(Image, "Resampling", Image).LANCZOS
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest > MODEL_MAX_EDGE:
            scale = MODEL_MAX_EDGE / float(longest)
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(size, resample)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=MODEL_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()


def _default_http_post(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 配置项 URL
            return int(getattr(response, "status", 200)), response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except Exception:  # noqa: BLE001
            payload = b""
        return int(exc.code), payload
    except (socket.timeout, TimeoutError) as exc:
        raise BirdClientError(BIRD_API_TIMEOUT, f"请求方舟超时（{timeout:.0f}s）") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise BirdClientError(BIRD_API_TIMEOUT, f"请求方舟超时（{timeout:.0f}s）") from exc
        raise BirdClientError(BIRD_API_ERROR, f"无法连接方舟接口：{reason}") from exc
    except OSError as exc:
        raise BirdClientError(BIRD_API_ERROR, f"网络错误：{exc}") from exc


def _extract_error_message(body: bytes, status: int) -> str:
    text = body.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            text = str(error["message"])
        elif isinstance(error, str) and error:
            text = error
        elif payload.get("message"):
            text = str(payload["message"])
    cleaned = " ".join(text.split())
    return cleaned[:300] or f"HTTP {status}"


def _auth_hint(status: int) -> str:
    if status in (401, 403):
        return "（请检查 API Key 是否有权限访问该模型）"
    if status == 404:
        return "（请检查 API 端点是否为完整 …/chat/completions 或 …/responses、模型名/endpoint id 是否正确）"
    if status == 429:
        return "（请求过于频繁或额度不足）"
    return ""


class ArkBirdClient:
    """真实的火山方舟 doubao 多模态识别客户端。"""

    def __init__(
        self,
        config: BirdConfig,
        http_post: Optional[HttpPost] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._http_post = http_post or _default_http_post
        self._sleep = sleep
        #: Responses API 的联网搜索工具（用于让模型检索真实参考图）；失败一次后关闭
        self._web_search_enabled = config.protocol == "responses"
        #: 联网搜索不可用时的原因（如未开通插件），供上层提示
        self.web_search_error: Optional[str] = None

    # ---- 请求 --------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            status, raw = self._http_post(
                self.config.endpoint, self._headers(), body, REQUEST_TIMEOUT
            )
        except BirdClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - 注入实现可能抛任意异常
            raise BirdClientError(BIRD_API_ERROR, f"调用方舟接口失败：{exc}") from exc

        if status < 200 or status >= 300:
            message = _extract_error_message(raw, status)
            # 某些模型不接受 thinking 参数：去掉后重试一次（见 identify）
            if status == 400 and _thinking_rejected(message):
                raise _ThinkingRejected(message)
            raise BirdClientError(
                BIRD_API_ERROR,
                f"方舟接口返回 HTTP {status}：{message}{_auth_hint(status)}"
                f"（请求地址：{self.config.endpoint}）",
            )
        try:
            payload_out = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise BirdClientError(BIRD_API_ERROR, "方舟返回内容不是 JSON") from exc
        if not isinstance(payload_out, dict):
            raise BirdClientError(BIRD_API_ERROR, "方舟返回内容结构异常")
        return payload_out

    def _request_body(self, image_b64: str, with_thinking: bool) -> Dict[str, Any]:
        """按配置端点的协议构造请求体（chat / responses 二选一）。"""
        protocol = self.config.protocol
        if protocol == "responses":
            payload: Dict[str, Any] = {
                "model": self.config.model,
                "instructions": SYSTEM_PROMPT,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{image_b64}",
                            },
                            {"type": "input_text", "text": USER_PROMPT},
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_output_tokens": 1200,
            }
            if self._web_search_enabled:
                # 让模型联网检索真实参考图；账号未开通插件时会在 identify 中自动降级
                payload["tools"] = [{"type": "web_search"}]
        else:
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                            },
                            {"type": "text", "text": USER_PROMPT},
                        ],
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 1200,
            }
        if with_thinking:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _content_of(self, payload: Dict[str, Any]) -> str:
        if self.config.protocol == "responses":
            return self._content_of_responses(payload)
        return self._content_of_chat(payload)

    @staticmethod
    def _content_of_chat(payload: Dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果缺少 choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果 choices 结构异常")
        message = first.get("message")
        if not isinstance(message, dict):
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果缺少 message")
        content = message.get("content")
        if isinstance(content, list):
            # 少数情况下 content 是 [{type: "text", text: "..."}]
            parts = [str(part.get("text", "")) for part in content if isinstance(part, dict)]
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果内容为空")
        return content

    @staticmethod
    def _content_of_responses(payload: Dict[str, Any]) -> str:
        output = payload.get("output")
        if not isinstance(output, list) or not output:
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果缺少 output")
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        content = "".join(texts).strip()
        if not content:
            raise BirdClientError(BIRD_API_ERROR, "方舟返回结果内容为空")
        return content

    # ---- 公开 API ----------------------------------------------------------

    def identify(
        self, image_bytes: bytes, seed: str = "", photo_name: str = ""
    ) -> ParsedIdentification:
        if not self.config.endpoint_supported:
            raise BirdClientError(
                BIRD_API_ERROR,
                self.config.endpoint_warning
                or f"不支持的 API 端点：{self.config.endpoint}。{endpoint_hint()}",
            )
        try:
            prepared = prepare_image_bytes(image_bytes)
        except Exception as exc:  # noqa: BLE001
            raise BirdClientError(BIRD_PARSE_ERROR, f"无法读取照片用于识别：{exc}") from exc
        image_b64 = base64.b64encode(prepared).decode("ascii")

        with_thinking = True
        thinking_retried = False
        web_search_retried = False
        attempt = 0
        while True:
            attempt += 1
            try:
                payload = self._post(self._request_body(image_b64, with_thinking))
            except _ThinkingRejected:
                if thinking_retried:
                    raise BirdClientError(BIRD_API_ERROR, "模型拒绝 thinking 参数，重试仍失败")
                thinking_retried = True
                with_thinking = False
                continue
            except BirdClientError as exc:
                # 账号未开通「联网搜索」插件时，自动降级为普通识别（参考图留空）
                if (
                    self._web_search_enabled
                    and not web_search_retried
                    and _web_search_unavailable(exc.message)
                ):
                    self._web_search_enabled = False
                    self.web_search_error = exc.message
                    web_search_retried = True
                    continue
                retryable = exc.code == BIRD_API_TIMEOUT or "HTTP 429" in exc.message or any(
                    f"HTTP {code}" in exc.message for code in (500, 502, 503, 504)
                )
                if retryable and attempt < 2:
                    self._sleep(RETRY_DELAY)
                    continue
                raise
            content = self._content_of(payload)
            try:
                return parse_model_output(content)
            except BirdParseError as exc:
                raise BirdClientError(BIRD_PARSE_ERROR, str(exc)) from exc

    def request_reference_url(
        self,
        result: Any,
        *,
        rejected: Optional[List[str]] = None,
        _allow_retry: bool = True,
    ) -> Optional[Dict[str, str]]:
        """首轮没有可用参考图时，向模型再要一个「图片文件直链」。

        只在识别成功时调用；使用模型联网搜索能力（Responses API）或已有知识（Chat
        Completions）。``rejected`` 是之前不合格/不可下载的 URL，会带进提示词要求
        模型不要重复（例如模型给了网页或维基页面时）。返回
        ``{referenceImageUrl, referenceSource, referenceNote}``。
        """
        if not self.config.endpoint_supported or not getattr(result, "identified", False):
            return None
        species = str(getattr(result, "species", "") or "").strip()
        latin = str(getattr(result, "speciesLatin", "") or "").strip()
        if not species:
            return None
        rejected_section = ""
        if rejected:
            lines = "\n".join(f"- {url}" for url in rejected[-5:] if url)
            rejected_section = (
                "以下链接之前已经不合格（是网页/不可访问/下载失败），不要重复给出：\n"
                f"{lines}\n"
            )
        prompt = REFERENCE_RETRY_PROMPT.format(
            species=species, latin=latin or "—", rejected_section=rejected_section
        )
        if self.config.protocol == "responses":
            payload: Dict[str, Any] = {
                "model": self.config.model,
                "input": prompt,
                "max_output_tokens": 400,
                "temperature": 0.1,
            }
            if self._web_search_enabled:
                payload["tools"] = [{"type": "web_search"}]
        else:
            payload = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.1,
            }
        try:
            response = self._post(payload)
            content = self._content_of(response)
        except BirdClientError as exc:
            if self._web_search_enabled and _web_search_unavailable(exc.message):
                self._web_search_enabled = False
                self.web_search_error = exc.message
                if _allow_retry:
                    return self.request_reference_url(
                        result, rejected=rejected, _allow_retry=False
                    )
            return None
        return _parse_reference_payload(content)

    def verify(self) -> Tuple[bool, str, Optional[int]]:
        """用一条极小的 text-only 请求验证 Key / 模型 / 端点 / 网络是否可用。"""
        if not self.config.endpoint_supported:
            return (
                False,
                self.config.endpoint_warning
                or f"不支持的 API 端点：{self.config.endpoint}。{endpoint_hint()}",
                None,
            )
        if self.config.protocol == "responses":
            payload = {
                "model": self.config.model,
                "input": "ping，只回复 ok",
                "max_output_tokens": 16,
            }
        else:
            payload = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": "ping，只回复 ok"}],
                "max_tokens": 16,
            }
        started = time.monotonic()
        try:
            response = self._post(payload)
            if self.config.protocol == "responses":
                if not isinstance(response.get("output"), list):
                    raise BirdClientError(BIRD_API_ERROR, "方舟返回结果缺少 output")
            else:
                if not isinstance(response.get("choices"), list):
                    raise BirdClientError(BIRD_API_ERROR, "方舟返回结果缺少 choices")
            latency = int((time.monotonic() - started) * 1000)
            protocol_label = "Responses API" if self.config.protocol == "responses" else "Chat Completions"
            return True, f"连接成功（{self.config.model} · {protocol_label}）", latency
        except _ThinkingRejected:  # pragma: no cover - verify 不带 thinking
            return False, "模型拒绝了请求参数", None
        except BirdClientError as exc:
            return False, exc.message, None


class _ThinkingRejected(Exception):
    """内部信号：400 且错误信息指向 thinking 参数。"""


def _thinking_rejected(message: str) -> bool:
    lowered = message.lower()
    return "thinking" in lowered and any(
        word in lowered for word in ("unknown", "invalid", "unrecognized", "not support", "不支持")
    )


def _web_search_unavailable(message: str) -> bool:
    """识别「联网搜索插件未开通/不可用」类错误，用于自动降级。"""
    lowered = (message or "").lower()
    if "toolnotopen" in lowered:
        return True
    if "web search" not in lowered and "web_search" not in lowered:
        return False
    return any(
        word in lowered
        for word in ("not activated", "activate", "not open", "no permission", "unknown", "invalid", "not support")
    )


_IMAGE_URL_RE = re.compile(
    r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>]*)?', re.IGNORECASE
)


def _parse_reference_payload(content: str) -> Optional[Dict[str, str]]:
    """解析「再要一次参考图 URL」的模型输出（JSON 优先，正则兜底）。"""
    payload: Optional[dict] = None
    try:
        payload = extract_json_object(content)
    except Exception:  # noqa: BLE001 - 交给正则兜底
        payload = None
    url = ""
    source = ""
    note = ""
    if isinstance(payload, dict):
        url = str(payload.get("referenceImageUrl") or "").strip()
        source = str(payload.get("referenceSource") or "").strip()
        note = str(payload.get("referenceNote") or "").strip()
    if not url:
        match = _IMAGE_URL_RE.search(content or "")
        if match:
            url = match.group(0).strip()
    if not url:
        return None
    return {
        "referenceImageUrl": url,
        "referenceSource": source,
        "referenceNote": note,
    }


# ---- 模拟客户端 ------------------------------------------------------------

MOCK_SPECIES = (
    ("鹭科", "Ardeidae", "白鹭属", "Egretta", "白鹭", "Egretta garzetta", "Little Egret"),
    ("鹭科", "Ardeidae", "鹭属", "Ardea", "苍鹭", "Ardea cinerea", "Grey Heron"),
    (
        "鹭科",
        "Ardeidae",
        "夜鹭属",
        "Nycticorax",
        "夜鹭",
        "Nycticorax nycticorax",
        "Black-crowned Night Heron",
    ),
    (
        "䴙䴘科",
        "Podicipedidae",
        "小䴙䴘属",
        "Tachybaptus",
        "小䴙䴘",
        "Tachybaptus ruficollis",
        "Little Grebe",
    ),
    ("翠鸟科", "Alcedinidae", "翠鸟属", "Alcedo", "翠鸟", "Alcedo atthis", "Common Kingfisher"),
)


def _mock_index(seed: str) -> int:
    digest = hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()
    return int(digest[:8], 16)


def mock_reference_image(parsed: ParsedIdentification) -> ReferenceImage:
    """生成一张内存参考图（深色渐变 + 英文标注；不落盘）。"""
    width, height = 800, 600
    image = Image.new("RGB", (width, height), (18, 24, 32))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / float(height)
        color = (
            int(18 + 40 * ratio),
            int(28 + 44 * ratio),
            int(40 + 52 * ratio),
        )
        draw.line([(0, y), (width, y)], fill=color)
    draw.rectangle([(40, 40), (width - 40, height - 40)], outline=(255, 191, 71), width=3)
    latin = parsed.result.speciesLatin or parsed.result.species
    lines = [
        "MOCK REFERENCE",
        latin or "unknown species",
        parsed.result.familyLatin or parsed.result.family,
    ]
    draw.text((70, height // 2 - 60), "\n".join(lines), fill=(240, 244, 250), spacing=18)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return ReferenceImage(
        data=buffer.getvalue(),
        content_type="image/jpeg",
        source_url="",
        source_label="Mock Reference",
    )


class MockBirdClient:
    """离线模拟：确定性结果 + 内存参考图（e2e/演示用）。"""

    def __init__(self, config: BirdConfig, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self._sleep = sleep

    def identify(
        self, image_bytes: bytes, seed: str = "", photo_name: str = ""
    ) -> ParsedIdentification:
        if self.config.mock_delay:
            self._sleep(self.config.mock_delay)
        if self.config.mock_fail:
            raise BirdClientError(BIRD_API_ERROR, "模拟失败：PICTUREPICK_BIRD_MOCK_FAIL=1")
        if "notbird" in (photo_name or seed).lower():
            result = BirdResult(
                identified=False,
                reason="模拟结果：画面中没有识别到鸟",
                note="（模拟识别结果，仅用于测试）",
                referenceSource="",
            )
            return ParsedIdentification(result=result)

        index = _mock_index(seed or "picturepick")
        family, family_latin, genus, genus_latin, species, species_latin, common = MOCK_SPECIES[
            index % len(MOCK_SPECIES)
        ]
        confidence = 0.80 + 0.03 * (index % 5)
        alternatives = []
        next_species = MOCK_SPECIES[(index + 1) % len(MOCK_SPECIES)]
        alternatives.append(
            BirdAlternative(
                species=next_species[4],
                speciesLatin=next_species[5],
                confidence=round(max(0.0, 1.0 - confidence - 0.05), 3),
            )
        )
        result = BirdResult(
            identified=True,
            family=family,
            familyLatin=family_latin,
            genus=genus,
            genusLatin=genus_latin,
            species=species,
            speciesLatin=species_latin,
            commonNameEn=common,
            confidence=round(confidence, 3),
            note="（模拟识别结果，仅用于测试）",
            alternatives=alternatives,
            referenceSource="Mock Reference",
            referenceNote="模拟参考图，仅用于对照界面演示。",
        )
        return ParsedIdentification(result=result)

    def verify(self) -> Tuple[bool, str, Optional[int]]:
        if self.config.mock_fail:
            return False, "模拟模式：已设置 PICTUREPICK_BIRD_MOCK_FAIL=1", None
        return True, "模拟模式：无需真实连接", 0

    def reference(self, parsed: ParsedIdentification) -> Optional[ReferenceImage]:
        return mock_reference_image(parsed)


def build_client(config: BirdConfig) -> object:
    """按配置返回真实/模拟客户端。"""
    if config.mock:
        return MockBirdClient(config)
    return ArkBirdClient(config)
