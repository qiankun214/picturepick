"""生成用于演示与自动化测试的样例照片目录。

用法：
    python scripts/make_sample_photos.py [输出目录] [--count 60]

生成：
- 根目录 + 若干子目录（模拟真实相册结构）
- 大小写混合的 .jpg / .jpeg 文件（应被扫描）
- 少量 .png / .txt / .JPG 之外的干扰文件（不应被误扫，.JPG 大写应被扫描）
全部 JPEG 用 Pillow 现场生成，无外部图片依赖。
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PALETTES = [
    ((18, 26, 46), (86, 132, 255)),
    ((40, 18, 40), (230, 90, 140)),
    ((12, 40, 32), (60, 200, 150)),
    ((48, 32, 12), (240, 170, 60)),
    ((24, 24, 30), (200, 200, 220)),
    ((10, 34, 48), (80, 200, 230)),
]


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_photo(path: Path, index: int, total: int, width: int, height: int) -> None:
    top, bottom = PALETTES[index % len(PALETTES)]
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):  # 垂直渐变
        t = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(
                int(top[0] + (bottom[0] - top[0]) * t),
                int(top[1] + (bottom[1] - top[1]) * t),
                int(top[2] + (bottom[2] - top[2]) * t),
            ),
        )
    # 几何装饰，让缩略图之间有可辨识差异
    rnd = random.Random(index)
    for _ in range(6):
        x0 = rnd.randint(0, width - 40)
        y0 = rnd.randint(0, height - 40)
        d = rnd.randint(40, max(60, width // 4))
        color = tuple(min(255, c + 60) for c in bottom)
        draw.ellipse([x0, y0, x0 + d, y0 + d], outline=color, width=max(2, d // 20))
    label = f"{index + 1:03d} / {total:03d}"
    font_big = _font(max(28, width // 9))
    tw = draw.textlength(label, font=font_big)
    draw.text(
        ((width - tw) / 2, height * 0.72),
        label,
        fill=(235, 238, 245),
        font=font_big,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=88)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 PicturePick 演示用 JPEG 样例目录")
    parser.add_argument("output", nargs="?", default=str(Path(__file__).resolve().parent.parent / "samples" / "photos"))
    parser.add_argument("--count", type=int, default=60, help="生成的照片数量（默认 60）")
    parser.add_argument("--seed", type=int, default=20241024, help="随机种子")
    args = parser.parse_args()

    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)

    subdirs = ["", "2024-春游", "2024-春游/精选", "2024-秋日", "生活随手拍", "旅行/日本"]
    for index in range(args.count):
        width = random.choice([1600, 1920, 2400])
        height = int(width * random.choice([2 / 3, 3 / 4, 9 / 16]))
        sub = random.choice(subdirs)
        ext = random.choice([".jpg", ".jpg", ".jpg", ".JPG", ".jpeg"])
        path = root / sub / f"IMG_{index + 1:04d}{ext}"
        make_photo(path, index, args.count, width, height)

    # 干扰文件：不应被扫描为照片
    (root / "说明.txt").write_text("这是干扰文件，不应出现在照片列表中。\n", encoding="utf-8")
    (root / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    (root / "旧照片_jpg.bak").write_bytes(b"backup")

    print(f"OK generated {args.count} jpg photos under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
