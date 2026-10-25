"""
生成 PicturePick 桌面端图标（512x512 icon.png + 多尺寸 icon.ico）。

画法：深色圆角底 + 相机机身/镜头 + 琥珀色星芒（"pick" 的意象）。
用法：cd desktop && python scripts/make_icon.py
依赖：Pillow（系统已安装）
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")

BG_TOP = (22, 26, 33, 255)
BG_BOTTOM = (11, 13, 17, 255)
BODY = (38, 46, 58, 255)
BODY_EDGE = (92, 106, 128, 255)
LENS_OUTER = (58, 70, 88, 255)
LENS_MID = (26, 32, 41, 255)
LENS_CORE = (10, 12, 16, 255)
ACCENT = (255, 183, 77, 255)
ACCENT_SOFT = (255, 213, 128, 255)
HIGHLIGHT = (255, 255, 255, 60)


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def vertical_gradient(width: int, height: int, top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    """竖向线性渐变。"""
    gradient = Image.new("RGBA", (1, height))
    pixels = gradient.load()
    for y in range(height):
        t = y / max(1, height - 1)
        pixels[0, y] = tuple(lerp(top[i], bottom[i], t) for i in range(3)) + (255,)
    return gradient.resize((width, height), Image.Resampling.BICUBIC)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = vertical_gradient(SIZE, SIZE, BG_TOP, BG_BOTTOM)
    base.paste(gradient, (0, 0), rounded_mask(SIZE, 108))

    # 微弱顶部高光
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-120, -260, SIZE + 120, 220], fill=(120, 170, 255, 34))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(48)))

    draw = ImageDraw.Draw(base)

    # 相机机身
    body_box = [78, 176, 434, 386]
    draw.rounded_rectangle(body_box, radius=56, fill=BODY, outline=BODY_EDGE, width=6)

    # 取景器凸起
    draw.rounded_rectangle([196, 142, 316, 190], radius=22, fill=BODY, outline=BODY_EDGE, width=6)

    # 闪光灯窗
    draw.rounded_rectangle([110, 206, 176, 236], radius=14, fill=(72, 86, 106, 255))
    draw.rounded_rectangle([116, 212, 170, 220], radius=6, fill=ACCENT_SOFT)

    # 镜头：多层同心圆制造纵深
    cx, cy = 256, 300
    draw.ellipse([cx - 104, cy - 104, cx + 104, cy + 104], fill=LENS_OUTER)
    draw.ellipse([cx - 92, cy - 92, cx + 92, cy + 92], fill=(20, 24, 30, 255), outline=(120, 138, 165, 255), width=4)
    draw.ellipse([cx - 72, cy - 72, cx + 72, cy + 72], fill=LENS_MID)
    draw.ellipse([cx - 48, cy - 48, cx + 48, cy + 48], fill=LENS_CORE, outline=(46, 58, 76, 255), width=4)

    # 镜片反光
    draw.ellipse([cx - 34, cy - 40, cx - 2, cy - 8], fill=(150, 200, 255, 120))
    draw.ellipse([cx + 6, cy + 6, cx + 30, cy + 30], fill=(255, 214, 150, 70))

    # 星芒（pick 意象）
    star_cx, star_cy, long_r, short_r = 372, 168, 54, 17
    star = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    star_draw = ImageDraw.Draw(star)
    for angle in (0, 90):
        if angle == 0:
            box = [star_cx - long_r, star_cy - short_r, star_cx + long_r, star_cy + short_r]
        else:
            box = [star_cx - short_r, star_cy - long_r, star_cx + short_r, star_cy + long_r]
        star_draw.ellipse(box, fill=ACCENT)
    star_draw.ellipse([star_cx - 26, star_cy - 26, star_cx + 26, star_cy + 26], fill=ACCENT_SOFT)
    base.alpha_composite(star)

    # 机身顶部柔和高光（替代刺眼的通栏亮条）
    highlight = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).rounded_rectangle([110, 192, 402, 206], radius=7, fill=(255, 255, 255, 64))
    base.alpha_composite(highlight.filter(ImageFilter.GaussianBlur(6)))

    # 整体轻微外发光
    halo = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle([6, 6, SIZE - 7, SIZE - 7], radius=110, outline=(120, 170, 255, 46), width=10)
    base.alpha_composite(halo.filter(ImageFilter.GaussianBlur(18)))

    png_path = os.path.join(OUT_DIR, "icon.png")
    base.save(png_path, format="PNG")
    print(f"wrote {png_path} ({base.size[0]}x{base.size[1]})")

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    base.save(ico_path, format="ICO", sizes=icon_sizes)
    print(f"wrote {ico_path} sizes={[s[0] for s in icon_sizes]}")


if __name__ == "__main__":
    main()
