"""把模型输出解析成结构化识别结果（docs/BIRDID-CONTRACT.md §4.3）。

模型经常带 Markdown 围栏、前后说明或缺失字段，解析层必须尽量容错；
真正的 JSON 损坏才让任务失败（``BIRD_PARSE_ERROR``）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..models import BirdAlternative, BirdResult

MAX_TEXT = 200
MAX_ALTERNATIVES = 3

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


class BirdParseError(Exception):
    """模型输出无法解析为识别结果。"""


@dataclass
class ParsedIdentification:
    """解析结果 + 仅后端使用的参考图 URL（不暴露给前端）。"""

    result: BirdResult
    reference_image_url: str = ""


def extract_json_object(text: str) -> Dict[str, Any]:
    """从任意文本中提取第一个 JSON 对象。"""
    raw = (text or "").strip()
    if not raw:
        raise BirdParseError("模型返回内容为空")

    fence = _FENCE.search(raw)
    if fence and fence.group(1).strip():
        raw = fence.group(1).strip()

    start = raw.find("{")
    if start < 0:
        raise BirdParseError(f"模型输出中没有 JSON 对象：{_preview(raw)}")
    candidate = raw[start:]
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(candidate)
    except ValueError:
        end = candidate.rfind("}")
        if end > 0:
            try:
                payload = json.loads(candidate[: end + 1])
            except ValueError as exc:
                raise BirdParseError(f"模型输出不是合法 JSON：{_preview(raw)}") from exc
        else:
            raise BirdParseError(f"模型输出不是合法 JSON：{_preview(raw)}")
    if not isinstance(payload, dict):
        raise BirdParseError("模型输出的 JSON 顶层必须是对象")
    return payload


def _preview(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()[:limit]


def _confidence(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, min(1.0, number))


def _alternatives(value: Any) -> List[BirdAlternative]:
    if not isinstance(value, list):
        return []
    collected: List[BirdAlternative] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        species = _text(item.get("species"))
        if not species:
            continue
        collected.append(
            BirdAlternative(
                species=species,
                speciesLatin=_text(item.get("speciesLatin")),
                confidence=_confidence(item.get("confidence")),
            )
        )
        if len(collected) >= MAX_ALTERNATIVES:
            break
    return collected


def parse_model_output(text: str) -> ParsedIdentification:
    """解析模型输出；结构不可用时抛 :class:`BirdParseError`。"""
    payload = extract_json_object(text)

    species = _text(payload.get("species"))
    identified_raw = payload.get("identified")
    if isinstance(identified_raw, bool):
        identified = identified_raw
    else:
        # 模型没给 identified 时，有 species 就当作识别成功。
        identified = bool(species)

    reason = _text(payload.get("reason"))
    if not identified:
        result = BirdResult(
            identified=False,
            reason=reason or "模型判断画面中没有可识别的鸟",
            note=_text(payload.get("note")),
            referenceSource=_text(payload.get("referenceSource")),
        )
        return ParsedIdentification(result=result)

    if not species:
        raise BirdParseError("模型输出缺少 species（鸟种中文名）")

    result = BirdResult(
        identified=True,
        family=_text(payload.get("family")),
        familyLatin=_text(payload.get("familyLatin")),
        genus=_text(payload.get("genus")),
        genusLatin=_text(payload.get("genusLatin")),
        species=species,
        speciesLatin=_text(payload.get("speciesLatin")),
        commonNameEn=_text(payload.get("commonNameEn")),
        confidence=_confidence(payload.get("confidence")),
        note=_text(payload.get("note")),
        alternatives=_alternatives(payload.get("alternatives")),
        referenceSource=_text(payload.get("referenceSource")),
        referenceNote=_text(payload.get("referenceNote")),
    )
    reference_url = _text(payload.get("referenceImageUrl"), limit=500)
    return ParsedIdentification(result=result, reference_image_url=reference_url)
