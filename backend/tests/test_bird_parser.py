"""AI 识鸟：模型输出解析测试（docs/BIRDID-CONTRACT.md §4.3）。"""

from __future__ import annotations

import pytest

from picturepick.birdid.parser import BirdParseError, extract_json_object, parse_model_output

GOOD = """
{
  "identified": true,
  "family": "鹭科",
  "familyLatin": "Ardeidae",
  "genus": "白鹭属",
  "genusLatin": "Egretta",
  "species": "白鹭",
  "speciesLatin": "Egretta garzetta",
  "commonNameEn": "Little Egret",
  "confidence": 0.92,
  "note": "全身白色，黑腿黄脚",
  "alternatives": [
    {"species": "黄嘴白鹭", "speciesLatin": "Egretta eulophotes", "confidence": 4},
    {"species": "大白鹭", "speciesLatin": "Ardea alba", "confidence": 0.02},
    {"species": "中白鹭", "speciesLatin": "Ardea intermedia", "confidence": 0.01},
    {"species": "多余条目", "speciesLatin": "X y", "confidence": 0.01}
  ],
  "referenceImageUrl": "https://upload.wikimedia.org/wikipedia/commons/x.jpg",
  "referenceSource": "Wikimedia Commons",
  "referenceNote": "白鹭典型特征"
}
"""


def test_parse_fenced_json_with_leading_text():
    text = "这是识别结果：\n```json\n" + GOOD + "\n```\n希望有帮助"
    parsed = parse_model_output(text)
    result = parsed.result
    assert result.identified is True
    assert result.family == "鹭科"
    assert result.genus == "白鹭属"
    assert result.species == "白鹭"
    assert result.speciesLatin == "Egretta garzetta"
    assert result.confidence == pytest.approx(0.92)
    # confidence 越界被夹到 [0,1]，备选最多 3 条
    assert [alt.species for alt in result.alternatives] == ["黄嘴白鹭", "大白鹭", "中白鹭"]
    assert result.alternatives[0].confidence == 1.0
    assert parsed.reference_image_url.startswith("https://upload.wikimedia.org/")


def test_parse_json_without_fence_and_trailing_comment():
    text = GOOD + "\n以上是结果。"
    parsed = parse_model_output(text)
    assert parsed.result.species == "白鹭"


def test_parse_identified_false():
    parsed = parse_model_output('{"identified": false, "reason": "画面中没有鸟"}')
    assert parsed.result.identified is False
    assert "没有鸟" in parsed.result.reason
    assert parsed.result.species == ""


def test_parse_identified_false_without_reason_uses_default():
    parsed = parse_model_output('{"identified": false}')
    assert parsed.result.identified is False
    assert parsed.result.reason


def test_parse_missing_identified_with_species_treated_as_identified():
    parsed = parse_model_output('{"species": "苍鹭"}')
    assert parsed.result.identified is True
    assert parsed.result.species == "苍鹭"


def test_parse_identified_true_without_species_raises():
    with pytest.raises(BirdParseError):
        parse_model_output('{"identified": true, "family": "鹭科"}')


def test_parse_invalid_json_raises():
    with pytest.raises(BirdParseError):
        parse_model_output("模型抽风了，没有 JSON")


def test_parse_empty_output_raises():
    with pytest.raises(BirdParseError):
        parse_model_output("   ")


def test_parse_captures_reference_page_url():
    payload = (
        '{"identified": true, "species": "白鹭", '
        '"referenceImageUrl": "https://img.example.com/egret.jpg", '
        '"referenceSource": "中国鸟类图库", '
        '"referencePageUrl": "https://www.example.com/egret"}'
    )
    parsed = parse_model_output(payload)
    assert parsed.result.referencePageUrl == "https://www.example.com/egret"


def test_parse_reference_page_url_falls_back_to_note_or_image_url():
    with_note = parse_model_output(
        '{"identified": true, "species": "白鹭", '
        '"referenceImageUrl": "https://img.example.com/egret.jpg", '
        '"referenceNote": "https://www.example.com/egret"}'
    )
    assert with_note.result.referencePageUrl == "https://www.example.com/egret"

    image_only = parse_model_output(
        '{"identified": true, "species": "白鹭", '
        '"referenceImageUrl": "https://img.example.com/egret.jpg"}'
    )
    assert image_only.result.referencePageUrl == "https://img.example.com/egret.jpg"


def test_parse_truncates_long_text_and_non_numeric_confidence():
    payload = (
        '{"identified": true, "species": "白鹭", "note": "'
        + "长" * 500
        + '", "confidence": "高", "family": 123}'
    )
    parsed = parse_model_output(payload)
    assert parsed.result.confidence is None
    assert len(parsed.result.note) <= 200
    assert parsed.result.family == "123"


def test_extract_json_object_returns_first_object():
    payload = extract_json_object('前言 {"a": 1} 后记 {"b": 2}')
    assert payload == {"a": 1}
