"""AI 识鸟：EXIF 无损写入测试（docs/BIRDID-CONTRACT.md §7）。"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from PIL import Image, ExifTags

from helpers import make_jpeg
from picturepick.birdid.exif import (
    TAG_IMAGE_DESCRIPTION,
    TAG_XP_COMMENT,
    TAG_XP_KEYWORDS,
    TAG_XP_SUBJECT,
    TAG_XP_TITLE,
    write_bird_exif,
)
from picturepick.models import BirdObservation


def make_observation(**overrides) -> BirdObservation:
    values = dict(
        family="鹭科",
        familyLatin="Ardeidae",
        genus="白鹭属",
        genusLatin="Egretta",
        species="白鹭",
        speciesLatin="Egretta garzetta",
        commonNameEn="Little Egret",
        confidence=0.92,
        note="全身白色",
        referenceSource="Wikimedia Commons",
        model="doubao-seed-2-1-lite",
        adoptedAt="2026-10-25T16:30:00+08:00",
        exifWritten=False,
    )
    values.update(overrides)
    return BirdObservation(**values)


def make_jpeg_with_exif(path: Path) -> Path:
    image = Image.new("RGB", (120, 80), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "TestMake"
    exif[0x0110] = "TestModel"
    exif[0x0131] = "PicturePickTest"
    exif[0x010E] = "old description"
    image.save(path, format="JPEG", quality=90, exif=exif)
    return path


def image_payload(data: bytes) -> bytes:
    """返回 SOS（0xFFDA）之后的图像数据，用于校验未被重编码。"""
    index = data.find(b"\xff\xda")
    assert index > 0
    return data[index:]


def test_write_exif_to_plain_jpeg(tmp_path):
    path = make_jpeg(tmp_path / "plain.jpg", color=(20, 30, 40))
    before = path.read_bytes()
    outcome = write_bird_exif(str(path), make_observation())
    assert outcome.written is True, outcome.error
    assert outcome.error is None

    after = path.read_bytes()
    assert image_payload(after) == image_payload(before)

    with Image.open(path) as image:
        tags = dict(image.getexif())
        assert image.size == (64, 48)
    assert tags[TAG_IMAGE_DESCRIPTION] == (
        "Bird: Egretta garzetta; Family: Ardeidae; Genus: Egretta"
    )
    assert tags[TAG_XP_TITLE].decode("utf-16-le") == "白鹭 Egretta garzetta"
    assert "鹭科" in tags[TAG_XP_SUBJECT].decode("utf-16-le")
    keywords = tags[TAG_XP_KEYWORDS].decode("utf-16-le")
    assert "鸟类" in keywords and "白鹭属" in keywords and "Egretta garzetta" in keywords
    assert "PicturePick" in tags[TAG_XP_COMMENT].decode("utf-16-le")
    assert "92%" in tags[TAG_XP_COMMENT].decode("utf-16-le")


def test_write_exif_preserves_existing_tags_and_image_bytes(tmp_path):
    path = make_jpeg_with_exif(tmp_path / "camera.jpg")
    before = path.read_bytes()
    outcome = write_bird_exif(str(path), make_observation())
    assert outcome.written is True, outcome.error

    with Image.open(path) as image:
        tags = dict(image.getexif())
    assert tags[0x010F] == "TestMake"
    assert tags[0x0110] == "TestModel"
    assert tags[0x0131] == "PicturePickTest"
    assert tags[TAG_IMAGE_DESCRIPTION].startswith("Bird: Egretta garzetta")
    assert image_payload(path.read_bytes()) == image_payload(before)


def test_write_exif_restores_mtime(tmp_path):
    path = make_jpeg(tmp_path / "mtime.jpg")
    fixed = 1_700_000_000
    os.utime(path, (fixed, fixed))
    outcome = write_bird_exif(str(path), make_observation())
    assert outcome.written is True, outcome.error
    assert int(path.stat().st_mtime) == fixed


def test_write_exif_overwrites_previous_identification(tmp_path):
    path = make_jpeg(tmp_path / "twice.jpg")
    assert write_bird_exif(str(path), make_observation()).written is True
    outcome = write_bird_exif(
        str(path),
        make_observation(
            species="苍鹭",
            speciesLatin="Ardea cinerea",
            family="鹭科",
            genus="鹭属",
            confidence=0.55,
        ),
    )
    assert outcome.written is True, outcome.error
    with Image.open(path) as image:
        tags = dict(image.getexif())
    assert tags[TAG_XP_TITLE].decode("utf-16-le").startswith("苍鹭")
    keywords = tags[TAG_XP_KEYWORDS].decode("utf-16-le")
    assert "苍鹭" in keywords and "白鹭" not in keywords


def test_write_exif_missing_file_returns_error(tmp_path):
    outcome = write_bird_exif(str(tmp_path / "missing.jpg"), make_observation())
    assert outcome.written is False
    assert outcome.error and "不存在" in outcome.error


def test_write_exif_non_jpeg_returns_error(tmp_path):
    path = tmp_path / "not-a-jpeg.jpg"
    path.write_bytes(b"this is not a jpeg")
    outcome = write_bird_exif(str(path), make_observation())
    assert outcome.written is False
    assert outcome.error
    assert path.read_bytes() == b"this is not a jpeg"


def test_write_exif_leaves_no_temp_files(tmp_path):
    path = make_jpeg(tmp_path / "clean.jpg")
    assert write_bird_exif(str(path), make_observation()).written is True
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".picturepick-exif-")]
    assert leftovers == []


def test_write_exif_with_utf8_surrogate_free_text(tmp_path):
    path = make_jpeg(tmp_path / "unicode.jpg")
    outcome = write_bird_exif(
        str(path), make_observation(species="小䴙䴘", speciesLatin="Tachybaptus ruficollis")
    )
    assert outcome.written is True, outcome.error
    with Image.open(path) as image:
        tags = dict(image.getexif())
    assert tags[TAG_XP_TITLE].decode("utf-16-le") == "小䴙䴘 Tachybaptus ruficollis"
