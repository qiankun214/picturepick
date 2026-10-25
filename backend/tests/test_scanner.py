"""扫描模块单元测试：扩展名过滤、递归、排序、ID、错误。"""

from __future__ import annotations

import os

import pytest

from helpers import expected_photo_id, make_jpeg
from picturepick import scanner


def test_recursive_scan_filters_extensions_case_insensitively(library):
    result = scanner.scan(str(library), recursive=True)

    assert result.count == 6
    assert result.recursive is True
    assert [photo.name for photo in result.photos] == [
        "a.jpg",
        "b.JPG",
        "c.jpeg",
        "d.jpg",
        "e.JPEG",
        "f.jpg",
    ]
    assert all(photo.name.lower().endswith((".jpg", ".jpeg")) for photo in result.photos)
    assert "note.txt" not in [photo.name for photo in result.photos]
    assert "skip.png" not in [photo.name for photo in result.photos]
    assert "readme.md" not in [photo.name for photo in result.photos]


def test_non_recursive_scan_only_top_level(library):
    result = scanner.scan(str(library), recursive=False)

    assert result.count == 3
    assert result.recursive is False
    assert [photo.relative_path for photo in result.photos] == ["a.jpg", "b.JPG", "c.jpeg"]


def test_scan_sorts_case_insensitively(library):
    make_jpeg(library / "Zebra.jpg")
    make_jpeg(library / "apple.JPG")
    result = scanner.scan(str(library), recursive=False)
    names = [photo.name for photo in result.photos]
    # 不区分大小写升序：apple, a, b, c, Zebra 中 a 与 apple 按完整相对路径排序
    assert names.index("apple.JPG") < names.index("Zebra.jpg")
    assert names.index("a.jpg") < names.index("b.JPG") < names.index("c.jpeg")


def test_relative_paths_use_forward_slashes_and_are_relative(library):
    result = scanner.scan(str(library), recursive=True)
    for photo in result.photos:
        assert "\\" not in photo.relative_path
        assert not os.path.isabs(photo.relative_path)
        assert photo.name.lower().endswith((".jpg", ".jpeg"))
        assert photo.relative_path.endswith(photo.name)


def test_photos_are_subdirectories_of_root(library):
    result = scanner.scan(str(library), recursive=True)
    for photo in result.photos:
        assert scanner.is_within(result.root, photo.absolute_path)
        assert os.path.isabs(photo.absolute_path)
        assert os.path.isfile(photo.absolute_path)
        assert photo.size_bytes > 0


def test_photo_id_matches_contract_and_is_stable(library):
    first = scanner.scan(str(library), recursive=True)
    second = scanner.scan(str(library), recursive=True)

    assert [photo.id for photo in first.photos] == [photo.id for photo in second.photos]
    assert len({photo.id for photo in first.photos}) == first.count
    for photo in first.photos:
        assert photo.id == expected_photo_id(photo.absolute_path)
        assert len(photo.id) == 16
        assert photo.id == photo.id.lower()
        assert all(char in "0123456789abcdef" for char in photo.id)


def test_scan_result_root_is_absolute_normalized(library):
    result = scanner.scan(str(library) + os.sep + ".", recursive=False)
    assert os.path.isabs(result.root)
    assert os.path.normcase(result.root) == os.path.normcase(os.path.abspath(str(library)))


def test_invalid_directory_raises(tmp_path):
    with pytest.raises(scanner.InvalidDirectoryError):
        scanner.scan(str(tmp_path / "missing"), recursive=True)

    file_path = tmp_path / "a-file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(scanner.InvalidDirectoryError):
        scanner.scan(str(file_path), recursive=True)


def test_empty_directory_raises_no_photos(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(scanner.NoPhotosError):
        scanner.scan(str(empty), recursive=True)


def test_directory_with_only_non_photos_raises_no_photos(tmp_path):
    only_text = tmp_path / "texts"
    only_text.mkdir()
    (only_text / "a.txt").write_text("x", encoding="utf-8")
    (only_text / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(scanner.NoPhotosError):
        scanner.scan(str(only_text), recursive=True)


def test_is_within(library):
    photo = scanner.scan(str(library), recursive=True).photos[0]
    assert scanner.is_within(str(library), photo.absolute_path) is True
    assert scanner.is_within(str(library), str(library.parent / "outside.jpg")) is False
    # 父目录里的文件不算在 root 之内
    assert scanner.is_within(str(library / "sub"), str(library / "a.jpg")) is False
