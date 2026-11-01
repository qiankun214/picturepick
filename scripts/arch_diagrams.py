#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PicturePick 架构图生成器（纯 Pillow，无浏览器/无网络依赖）。

用法（在仓库根目录）::

    py -3.12 scripts\arch_diagrams.py            # 生成全部图到 docs/architecture/diagrams/
    py -3.12 scripts\arch_diagrams.py --only d2  # 只生成一张

为什么不用 Mermaid / mermaid-cli：本项目运行在受限的 DSH Windows 沙箱里，Chromium/Edge
headless 需要命名管道与 crashpad 进程，会被沙箱直接拒绝（0x5 拒绝访问），无法截图渲染。
因此这里用 Pillow 直接绘制，产物是确定性的 PNG，CI 里也能跑（Pillow 是已有依赖）。

设计约定：
* 所有布局坐标是「逻辑像素」，最终按 SUPERSAMPLE 倍超采样输出，保证高分屏清晰。
* 字体：微软雅黑（中文 + 拉丁）、Consolas（等宽路径/方法名）。
* 一张图一个函数，函数返回 Canvas；``main()`` 负责落盘。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# 调色板（深色 Lightroom 风格的工程蓝图）
# --------------------------------------------------------------------------
BG = "#0A101C"
PANEL = "#111A2B"
PANEL_HI = "#16223A"
BORDER = "#26364F"
GRID = "#1A2740"
TEXT = "#E9EFFA"
MUTED = "#9CAEC9"
DIM = "#6D7F9C"

BLUE = "#4F9CF9"      # 前端
GREEN = "#3DDC97"     # 后端
PURPLE = "#A78BFA"    # 桌面壳 / 进程
AMBER = "#F5B942"     # 外部服务 / AI
PINK = "#F472B6"      # 本机磁盘
CYAN = "#22D3EE"      # HTTP 契约
RED = "#F87171"       # DELETE / 风险
ORANGE = "#FB923C"    # PUT

METHOD_COLOR = {"GET": BLUE, "POST": GREEN, "PUT": ORANGE, "DELETE": RED}
DOMAIN_COLOR = [BLUE, GREEN, PURPLE, AMBER, PINK, CYAN]

SUPERSAMPLE = 2

FONT_DIR = Path("C:/Windows/Fonts")
FONT_FILES = {
    "regular": ["msyh.ttc", "msyh.ttf", "simhei.ttf", "seguiemj.ttf"],
    "bold": ["msyhbd.ttc", "msyhbd.ttf", "simhei.ttf"],
    "mono": ["consola.ttf", "cour.ttf"],
    "mono_bold": ["consolab.ttf", "consola.ttf"],
}
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _load_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for name in FONT_FILES[kind]:
        path = FONT_DIR / name
        if not path.is_file():
            continue
        try:
            font = ImageFont.truetype(str(path), size)
        except OSError:
            continue
        _font_cache[key] = font
        return font
    fallback = ImageFont.truetype("arial.ttf", size)
    _font_cache[key] = fallback
    return fallback


def mix(c1: str, c2: str, t: float) -> str:
    """按 t（0= c1, 1= c2）线性混合两个 #RRGGBB 颜色。"""
    a = tuple(int(c1[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i : i + 2], 16) for i in (1, 3, 5))
    out = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02X%02X%02X" % out


def tint(color: str, strength: float = 0.16) -> str:
    """把强调色压成深色背景色（用于 chip / 卡片底）。"""
    return mix(BG, color, strength)


def is_ascii(s: str) -> bool:
    """True 表示该串可安全用等宽字体（Consolas 不含 CJK 字形，混排会出豆腐块）。"""
    return all(ord(ch) < 128 for ch in s)


# --------------------------------------------------------------------------
# 画布 DSL
# --------------------------------------------------------------------------
class Canvas:
    def __init__(self, width: int, height: int, bg: str = BG) -> None:
        self.w = width
        self.h = height
        self.img = Image.new("RGB", (width * SUPERSAMPLE, height * SUPERSAMPLE), bg)
        self.d = ImageDraw.Draw(self.img)

    # -- 基础图元 ---------------------------------------------------------
    def rrect(self, x, y, w, h, r=10, fill=None, outline=None, width=1.4):
        s = SUPERSAMPLE
        self.d.rounded_rectangle(
            [round(x * s), round(y * s), round((x + w) * s), round((y + h) * s)],
            radius=round(r * s),
            fill=fill,
            outline=outline,
            width=max(1, round(width * s)),
        )

    def rect(self, x, y, w, h, fill=None, outline=None, width=1.4):
        self.rrect(x, y, w, h, r=0, fill=fill, outline=outline, width=width)

    def line(self, x1, y1, x2, y2, color=GRID, width=1.4, dash=None):
        s = SUPERSAMPLE
        if not dash:
            self.d.line([x1 * s, y1 * s, x2 * s, y2 * s], fill=color, width=max(1, round(width * s)))
            return
        import math

        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            return
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        pos = 0.0
        on_len, off_len = dash
        while pos < length:
            seg = min(on_len, length - pos)
            ax, ay = x1 + ux * pos, y1 + uy * pos
            bx, by = x1 + ux * (pos + seg), y1 + uy * (pos + seg)
            self.d.line([ax * s, ay * s, bx * s, by * s], fill=color, width=max(1, round(width * s)))
            pos += on_len + off_len

    def arrow(self, points, color=CYAN, width=1.8, head=9, dash=None, label=None,
              label_size=11, label_bg=BG, label_side=0.5, label_at=None):
        """折线箭头：points 为 [(x,y), ...]；最后一跳画箭头。"""
        import math

        pts = [(float(x), float(y)) for x, y in points]
        for i in range(len(pts) - 1):
            self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color=color, width=width, dash=dash)
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        left = (x2 - head * math.cos(ang - 0.42), y2 - head * math.sin(ang - 0.42))
        right = (x2 - head * math.cos(ang + 0.42), y2 - head * math.sin(ang + 0.42))
        self._poly([(x2, y2), left, right], fill=color)
        if label:
            if label_at is not None:
                lx, ly = label_at
            else:
                seg_start, seg_end = pts[0], pts[max(1, len(pts) - 1)]
                lx = seg_start[0] + (seg_end[0] - seg_start[0]) * label_side
                ly = seg_start[1] + (seg_end[1] - seg_start[1]) * label_side
            tw = self.tw(label, label_size)
            self.rrect(lx - tw / 2 - 5, ly - label_size * 0.85, tw + 10, label_size * 1.7,
                       r=4, fill=label_bg, outline=mix(BORDER, color, 0.35), width=0.8)
            self.text(lx, ly, label, size=label_size, color=color, anchor="mm")

    def _poly(self, points, fill=None, outline=None, width=1.4):
        s = SUPERSAMPLE
        pts = [(round(x * s), round(y * s)) for x, y in points]
        if fill:
            self.d.polygon(pts, fill=fill)
        if outline:
            self.d.line(pts + [pts[0]], fill=outline, width=max(1, round(width * s)))

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.2):
        s = SUPERSAMPLE
        self.d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
                       fill=fill, outline=outline, width=max(1, round(width * s)))

    # -- 文字 -------------------------------------------------------------
    def _font(self, kind: str, size: int):
        return _load_font(kind, max(6, round(size * SUPERSAMPLE)))

    def tw(self, s: str, size=13, bold=False, mono=False) -> float:
        kind = "bold" if bold else ("mono" if mono else "regular")
        if mono and bold:
            kind = "mono_bold"
        font = self._font(kind, size)
        return font.getlength(s) / SUPERSAMPLE

    def text(self, x, y, s, size=13, color=TEXT, bold=False, mono=False, anchor="lt", align=None):
        kind = "bold" if bold else ("mono" if mono else "regular")
        if mono and bold:
            kind = "mono_bold"
        s_ = SUPERSAMPLE
        font = self._font(kind, size)
        self.d.text((x * s_, y * s_), s, font=font, fill=color, anchor=anchor, align=align or "left")

    def wrap(self, s: str, max_w: float, size=13, bold=False, mono=False) -> list[str]:
        """按像素宽度贪心折行（CJK 无空格也能正确折断）。"""
        lines: list[str] = []
        cur = ""
        for ch in s:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            trial = cur + ch
            if self.tw(trial, size=size, bold=bold, mono=mono) > max_w and cur:
                lines.append(cur)
                cur = ch.lstrip() if ch == " " else ch
            else:
                cur = trial
        if cur or not lines:
            lines.append(cur)
        return lines

    def para(self, x, y, s, max_w, size=12.5, color=TEXT, bold=False, mono=False,
             line_h=1.62, max_lines=None) -> float:
        lines = self.wrap(s, max_w, size=size, bold=bold, mono=mono)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            if lines:
                lines[-1] = lines[-1].rstrip() + "…"
        lh = size * line_h
        for i, ln in enumerate(lines):
            self.text(x, y + i * lh, ln, size=size, color=color, bold=bold, mono=mono)
        return len(lines) * lh

    def bullets(self, x, y, items, max_w, size=12.5, color=MUTED, gap=1.62, dot_color=None) -> float:
        lh = size * gap
        cy = y
        for it in items:
            self.circle(x + 2.2, cy + size * 0.52, 1.9, fill=dot_color or mix(color, BG, 0.25))
            used = self.para(x + 9, cy, it, max_w - 9, size=size, color=color, line_h=gap)
            cy += max(lh, used)
        return cy - y

    def chip(self, x, y, label, color=TEXT, size=10.5, padx=6.5, pady=2.6, mono=False, bold=True) -> float:
        w = self.tw(label, size=size, bold=bold, mono=mono) + padx * 2
        h = size + pady * 2
        self.rrect(x, y, w, h, r=4, fill=tint(color, 0.20), outline=mix(color, BG, 0.55), width=0.9)
        self.text(x + padx, y + h / 2, label, size=size, color=color, bold=bold, mono=mono, anchor="lm")
        return w

    def method_chip(self, x, y, method, size=10) -> float:
        return self.chip(x, y, method, METHOD_COLOR.get(method, MUTED), size=size, mono=True)

    def step_num(self, cx, cy, n, color=CYAN, r=9.5, size=11):
        self.circle(cx, cy, r, fill=tint(color, 0.35), outline=mix(color, BG, 0.15), width=1.1)
        self.text(cx, cy, str(n), size=size, color=color, bold=True, anchor="mm")

    # -- 组合容器 ---------------------------------------------------------
    def panel(self, x, y, w, h, title=None, color=None, fill=PANEL, subtitle=None, radius=12,
              title_size=14.5, pad=14):
        self.rrect(x, y, w, h, r=radius, fill=fill, outline=BORDER, width=1.3)
        if color:
            self.rrect(x, y, 4.2, h, r=2, fill=color)
        if title:
            self.text(x + pad, y + pad - 1, title, size=title_size, color=TEXT if not color else mix(color, TEXT, 0.45), bold=True)
        if subtitle:
            self.text(x + pad, y + pad + title_size + 5, subtitle, size=10.8, color=DIM, mono=False)

    def band(self, x, y, w, h, title, color=GRID, label_size=11.5):
        self.rrect(x, y, w, h, r=10, fill=None, outline=color, width=1.0)
        tw = self.tw(title, size=label_size, bold=True)
        self.rrect(x + 14, y - label_size * 0.72, tw + 18, label_size * 1.44, r=4, fill=BG, outline=color, width=0.9)
        self.text(x + 23, y, title, size=label_size, color=mix(color, TEXT, 0.55), bold=True, anchor="lm")

    def card(self, x, y, w, h, title, lines=(), color=BLUE, title_size=13, line_size=11.4,
             fill=None, mono_lines=(), footer=None):
        self.rrect(x, y, w, h, r=8, fill=fill or mix(PANEL, color, 0.06), outline=mix(BORDER, color, 0.30), width=1.1)
        self.rrect(x, y, 3.2, h, r=1.6, fill=color)
        self.text(x + 11, y + 9, title, size=title_size, color=mix(color, TEXT, 0.35), bold=True)
        cy = y + 9 + title_size * 1.5
        for ln in lines:
            cy += self.para(x + 11, cy, ln, w - 20, size=line_size, color=MUTED, line_h=1.42) + 1
        for ln in mono_lines:
            self.text(x + 11, cy, ln, size=line_size - 0.6, color=mix(color, MUTED, 0.55), mono=True)
            cy += line_size * 1.5
        if footer:
            self.text(x + 11, y + h - 16, footer, size=9.8, color=DIM)
        return cy

    # -- 页眉 / 图例 ------------------------------------------------------
    def header(self, title, subtitle, kicker=None, legend=(), legend_y=None):
        if kicker:
            self.text(46, 34, kicker, size=11, color=CYAN, bold=True, mono=True)
        self.text(46, 52, title, size=27, color=TEXT, bold=True)
        self.text(46, 52 + 38, subtitle, size=13, color=MUTED)
        if legend:
            y = legend_y if legend_y is not None else self.h - 44
            x = 46
            self.text(x, y + 4, "图例", size=10.5, color=DIM, bold=True)
            x += 34
            for label, color in legend:
                w = self.chip(x, y, label, color, size=10)
                x += w + 8

    def footer_note(self, text, color=DIM):
        self.text(46, self.h - 26, text, size=10.2, color=color)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.save(path, format="PNG", optimize=True)
        return path


# --------------------------------------------------------------------------
# D1 · 系统上下文与部署拓扑
# --------------------------------------------------------------------------
def d1_context() -> Canvas:
    c = Canvas(1800, 1160)
    c.header(
        "系统上下文与部署拓扑",
        "三种启动形态共用同一份 FastAPI 后端与同一份前端产物；后端只监听 127.0.0.1，照片字节默认不出本机",
        kicker="PICTUREPICK · D1 / CONTEXT & DEPLOYMENT",
    )

    # ① 用户与前端载体 -----------------------------------------------------
    c.band(46, 146, 1708, 118, "① 用户与前端载体")
    cards1 = [
        ("系统浏览器 · Chrome / Edge", ["命令行模式的前端载体", "加载 Vite 构建产物（原生 DOM）"]),
        ("Electron 窗口 · 内置 Chromium", ["桌面版唯一 UI 载体", "同一份 webui 资源，渲染一致"]),
        ("终端 CLI", ["serve / scan / version 子命令", "无 UI，直接拉起后端进程"]),
    ]
    for i, (title, lines) in enumerate(cards1):
        x = 70 + i * 562
        c.card(x, 170, 534, 82, title, lines, color=[BLUE, PURPLE, MUTED][i], title_size=13.5)
        c.arrow([(x + 267, 256), (x + 267, 282)], color=mix(BORDER, CYAN, 0.6))

    # ② 启动形态 -----------------------------------------------------------
    c.band(46, 290, 1708, 152, "② 启动形态：三个入口，一个后端")
    modes = [
        ("开发模式 · scripts/dev.ps1", ["Vite dev server :5173", "proxy /api → uvicorn :8000"], "① 浏览器 :5173", BLUE),
        ("桌面模式 · scripts/build.ps1", ["Electron main → 选空闲端口", "PyInstaller sidecar exe 起 uvicorn"], "② 窗口 http://127.0.0.1:<port>", PURPLE),
        ("命令行模式 · python -m picturepick serve", ["uvicorn :8000 --no-open 或自动开浏览器", "可选 --dir 预扫描目录"], "③ 浏览器 :8000", MUTED),
    ]
    for i, (title, lines, foot, color) in enumerate(modes):
        x = 70 + i * 562
        c.rrect(x, 316, 534, 112, r=8, fill=mix(PANEL, color, 0.07), outline=mix(BORDER, color, 0.30), width=1.1)
        c.rrect(x, 316, 3.2, 112, r=1.6, fill=color)
        c.text(x + 12, 326, title, size=12.6, color=mix(color, TEXT, 0.35), bold=True)
        cy = 326 + 21
        for ln in lines:
            c.text(x + 12, cy, ln, size=10.8, color=MUTED, mono=is_ascii(ln))
            cy += 16
        c.text(x + 12, 398, foot, size=10.2, color=color, mono=is_ascii(foot))
        c.arrow([(x + 267, 442), (x + 267, 470)], color=mix(BORDER, CYAN, 0.6))

    # ③ 后端进程 -----------------------------------------------------------
    c.band(46, 478, 1708, 236, "③ 本机后端进程 · 同一份 FastAPI 应用（单进程 / 单 worker）")
    inner = [
        ("API 层", ["app.py 装配 + api.py 路由", "28 个 REST 端点 · /api 前缀", "models.py · pydantic 契约"], GREEN),
        ("会话态（内存）", ["SessionStore · 24h TTL", "照片元数据 + 轮次 + pick 计数", "进程重启即丢失"], BLUE),
        ("AI 识鸟服务", ["BirdService + 单线程 worker 队列", "Ark 双协议客户端 · 参考图代理", "任务态只在内存"], AMBER),
        ("静态托管", ["/assets → StaticFiles", "SPA fallback（API 前缀不吞）", "CORS 仅 localhost/127.0.0.1"], CYAN),
    ]
    for i, (title, lines, color) in enumerate(inner):
        x = 70 + i * 421
        c.card(x, 504, 393, 108, title, lines, color=color, title_size=12.8, line_size=10.9)
    c.text(70, 636, "uvicorn 单进程 · 无外部数据库 · 无消息队列 · 无服务发现（这是「单体」不是「微服务」）",
           size=11.4, color=DIM)

    # 层间箭头（③→④）
    for i, (cx, label, color) in enumerate([
        (238, "Pillow 读取 / 缩略图", PINK),
        (602, "复制文件 / 写 JSON", PINK),
        (966, "读配置 / 写 EXIF", PINK),
    ]):
        c.arrow([(cx, 714), (cx, 742)], color=mix(PINK, BORDER, 0.25), label=label, label_size=9.8)

    # ④ 本机资源 + 外部服务 ------------------------------------------------
    c.band(46, 750, 1120, 264, "④ 本机资源（唯一真实数据源）", color=PINK)
    res = [
        ("照片目录（只读）", ["目录由用户选择，后端递归扫描", ".jpg / .jpeg · 大小写不敏感", "GET /image 按需返回字节"], PINK),
        ("导出目录（写入）", ["最后一轮保留照片逐字节复制", "同名自动 -1 / -2，不覆盖", "POST /export-photos"], PINK),
        ("配置文件 + 日志", ["%APPDATA%\\PicturePick\\settings.json", "API Key 只存本机 · 接口只回掩码", "main.log / backend.log"], AMBER),
    ]
    for i, (title, lines, color) in enumerate(res):
        x = 70 + i * 364
        c.card(x, 782, 336, 106, title, lines, color=color, title_size=12.4, line_size=10.6)
    c.text(70, 906, "照片是唯一位移的「大文件」：全流程只在本机磁盘与 127.0.0.1 之间流转",
           size=11.2, color=MUTED)
    c.text(70, 926, "浏览器不持有 File 对象；AI 识鸟时仅当前一张照片压到 ≤1568px 后 base64 上传",
           size=11.2, color=MUTED)

    c.band(1192, 750, 562, 264, "⑤ 外部网络（唯一出网路径 · 可选）", color=AMBER)
    c.rrect(1216, 782, 514, 140, r=10, fill=mix(PANEL, AMBER, 0.12), outline=mix(BORDER, AMBER, 0.45), width=1.2)
    c.text(1232, 794, "火山方舟 Ark API", size=15, color=AMBER, bold=True)
    c.text(1232, 820, "doubao-seed-2-1-lite · 多模态识鸟", size=10.4, color=MUTED)
    for i, ln in enumerate([
        "完整端点原样 POST（chat/completions 或 /responses）",
        "Responses 带 tools:[{type:web_search}] 联网检索参考图",
        "Bearer API Key · 90s 超时 · 429/5xx 重试一次",
    ]):
        c.text(1232, 844 + i * 17, ln, size=9.9, color=mix(AMBER, MUTED, 0.55))
    c.text(1232, 936, "未配置 Key / PICTUREPICK_BIRD_MOCK=1 → 完全不出网", size=10.2, color=DIM)
    c.arrow([(1100, 616), (1100, 690), (1500, 690), (1500, 776)], color=mix(AMBER, BORDER, 0.15),
            label="HTTPS · 仅用户主动识鸟时", label_size=10, label_at=(1300, 690))

    x = 46
    c.text(x, 1056, "图例", size=10.5, color=DIM, bold=True)
    x += 40
    for label, color in [
        ("前端载体", BLUE), ("桌面/进程宿主", PURPLE), ("后端服务", GREEN),
        ("本机磁盘", PINK), ("外部云服务", AMBER), ("启动形态", MUTED),
    ]:
        x += c.chip(x, 1050, label, color, size=10) + 10
    c.footer_note("产物来源：scripts/arch_diagrams.py::d1_context() · 事实依据：README / docs/ARCHITECTURE.md §4.4 / backend/picturepick/app.py / desktop/src/main.ts")
    return c


# --------------------------------------------------------------------------
# D2 · 分层组件架构（前端实现什么 / 后端实现什么）
# --------------------------------------------------------------------------
def _layer_band(c: Canvas, y, label, color, cards, start_x=62, label_w=140,
                card_w=196, card_h=68, gap=12, text_size=9.9, max_x=1746):
    """一层：左侧标签列 + 卡片流式网格（高度按行数自动计算）。返回下一层 y。"""
    inner_x = start_x + label_w + 24
    per_row = max(1, (max_x - inner_x + gap) // (card_w + gap))
    rows = max(1, -(-len(cards) // per_row))
    height = 9 + rows * card_h + (rows - 1) * 10 + 9
    c.rrect(start_x, y, 24 + label_w, height, r=8, fill=mix(PANEL, color, 0.10),
            outline=mix(BORDER, color, 0.35), width=1.0)
    label_lines = label.split("\n")
    ly = y + height / 2 - (len(label_lines) - 1) * 8
    for ln in label_lines:
        c.text(start_x + 12, ly, ln, size=12.2, color=mix(color, TEXT, 0.30), bold=True, anchor="lm")
        ly += 16
    cx, cy = inner_x, y + 9
    for title, sub in cards:
        if cx + card_w > max_x + 1:
            cx, cy = inner_x, cy + card_h + 10
        c.rrect(cx, cy, card_w, card_h, r=7, fill=mix(PANEL_HI, color, 0.06),
                outline=mix(BORDER, color, 0.22), width=0.9)
        c.text(cx + 9, cy + 7, title, size=text_size + 1.0, color=mix(color, TEXT, 0.28), bold=True)
        sub_lines = c.wrap(sub, card_w - 16, size=text_size) if sub else []
        for i, ln in enumerate(sub_lines[:2]):
            c.text(cx + 9, cy + 25 + i * 12.6, ln, size=text_size, color=MUTED)
        cx += card_w + gap
    return y + height + 12


def d2_layers() -> Canvas:
    c = Canvas(1800, 1520)
    c.header(
        "分层组件架构：前端负责什么 / 后端负责什么",
        "前端 = 交互与状态镜像（不持有照片字节）· 后端 = 唯一真实数据源与文件操作者 · 契约 = docs/API.md 的 28 个端点",
        kicker="PICTUREPICK · D2 / LAYERED COMPONENTS",
    )

    # ---- 前端面板 --------------------------------------------------------
    c.panel(46, 138, 1708, 522, "前端 · TS + Vite + 原生 DOM（frontend/src · 19 个 TS 模块 4.5k 行 + CSS 3.9k 行）", color=BLUE)
    fe_layers = [
        ("视图层", BLUE, [
            ("start.ts", "启动页：选目录 / 载入 JSON / 设置"),
            ("picker.ts", "选片页三栏装配 · 38KB"),
            ("results.ts", "结果页：下载 / 导出 / 继续筛选"),
            ("filmstrip.ts", "底部胶片条 · 缩略图按需加载"),
            ("modal.ts", "通用模态框 · 统一 X 关闭"),
            ("toast.ts", "错误/成功提示"),
            ("screens.ts", "三屏切换骨架"),
            ("ui/bird/queue.ts", "右侧 AI 队列 · 1s 轮询"),
            ("ui/bird/resultModal.ts", "原片 vs 参考图对照"),
            ("ui/bird/settings.ts", "Key / 端点 / 模型设置"),
            ("ui/bird/info.ts", "左栏鸟种块"),
        ]),
        ("交互与渲染", CYAN, [
            ("viewer.ts", "滚轮缩放 / 拖拽平移 / 旋转 / 全屏"),
            ("main.ts", "视图路由 + 隐藏 input 复用"),
            ("directory.ts", "取绝对目录路径（桌面 IPC → 后端 dialog）"),
            ("styles.css", "深色 Lightroom 风格 · 70KB 单文件"),
            ("键盘快捷键", "←/→ P 空格 B 0/1 R F Esc"),
        ]),
        ("状态层", AMBER, [
            ("state.ts", "只存设备偏好 · intervalSeconds"),
            ("types.ts", "与 API.md 对齐的 TS 类型（手写）"),
        ]),
        ("接入层", GREEN, [
            ("api.ts", "22 个端点封装 · 契约 28 条实际用 23"),
            ("desktop.ts", "检测 window.picturePickDesktop 桥接"),
            ("构建链", "tsc --noEmit + vite build → backend/picturepick/webui"),
        ]),
    ]
    y = 184
    for label, color, cards in fe_layers:
        y = _layer_band(c, y, label, color, cards)

    # ---- 契约层 ----------------------------------------------------------
    cy = 684
    c.rrect(46, cy, 1708, 118, r=12, fill=mix(BG, CYAN, 0.10), outline=mix(CYAN, BG, 0.55), width=1.4)
    c.text(66, cy + 16, "HTTP / JSON 契约层", size=14.5, color=CYAN, bold=True)
    c.text(66, cy + 40, "docs/API.md = 前后端唯一准绳 · 28 个 REST 端点（/api 前缀）· 错误统一 {detail:{code,message}} · 图片走二进制响应", size=11.2, color=MUTED)
    flow = ["GET /api/sessions/current", "POST /api/sessions", "GET .../rounds/{i}/photos",
            "GET .../photos/{pid}/image?variant=thumb", "PUT .../pick", "POST .../rounds/next",
            "POST .../bird-tasks", "GET .../bird-tasks (poll)"]
    x = 66
    yy = cy + 74
    for f in flow:
        w = c.tw(f, size=10, mono=True) + 16
        if x + w > 1730:
            x = 66
            yy += 26
        c.rrect(x, yy, w, 21, r=4, fill=mix(PANEL, CYAN, 0.10), outline=mix(BORDER, CYAN, 0.30), width=0.8)
        c.text(x + 8, yy + 10.5, f, size=10, color=mix(CYAN, TEXT, 0.40), mono=True, anchor="lm")
        x += w + 8
    c.text(1700, cy + 18, "28 endpoints · 6 domains", size=11, color=CYAN, mono=True, anchor="rt")

    # ---- 后端面板 --------------------------------------------------------
    c.panel(46, 826, 1708, 530, "后端 · Python 3.12 + FastAPI + uvicorn + Pillow（backend/picturepick · 22 个模块 5.5k 行）", color=GREEN)
    be_layers = [
        ("接口层", GREEN, [
            ("app.py", "create_app：CORS / 异常 / 静态托管"),
            ("api.py", "28 个路由 · 430 行单文件"),
            ("models.py", "pydantic 请求/响应模型"),
            ("errors.py", "ApiError + 错误码常量"),
        ]),
        ("应用服务层", BLUE, [
            ("session.py", "会话 / 轮次状态机 · pick 计数"),
            ("restore.py", "从结果 JSON 重建轮次"),
            ("export.py", "结果 JSON 落盘 · 照片复制"),
            ("scanner.py", "递归扫描 · 照片 ID · 路径越界校验"),
            ("imaging.py", "缩略图 / 原图字节 · EXIF 转正"),
            ("fsbrowse.py", "盘符枚举 · 原生目录对话框"),
        ]),
        ("AI 识鸟\nbirdid/", AMBER, [
            ("service.py", "编排：配置 / 提交 / 采纳 / 参考图缓存"),
            ("tasks.py", "内存任务仓库 · 单线程 worker · 进度"),
            ("client.py", "Ark 双协议 · 重试 · ToolNotOpen 降级"),
            ("parser.py", "容错 JSON 解析 · 字段清洗"),
            ("prompt.py", "提示词（要求联网检索真实图片直链）"),
            ("reference.py", "SSRF 校验 · 下载 · 缩放（不落盘）"),
            ("exif.py", "无损写 EXIF（APP1 原位替换 + 原子替换）"),
            ("config.py", "环境变量 / settings.json 优先级 · 掩码"),
        ]),
        ("运行时", PURPLE, [
            ("cli.py", "serve / scan / version · 端口与就绪输出"),
            ("uvicorn", "单进程 · 仅绑定 127.0.0.1"),
            ("app.state", "注入 store / bird / webui_dir"),
            ("sidecar", "PyInstaller 打包为独立 exe"),
        ]),
    ]
    y = 872
    for label, color, cards in be_layers:
        y = _layer_band(c, y, label, color, cards)
    c.text(62, 1368, "关键约束：会话与 AI 任务全部在进程内存（重启即丢）；照片目录只读；唯一写盘动作 = 导出复制 / 结果 JSON / EXIF 元数据",
           size=11.2, color=DIM)

    # ---- 存储条 ----------------------------------------------------------
    c.rrect(46, 1392, 1708, 72, r=10, fill=mix(PANEL, PINK, 0.08), outline=mix(BORDER, PINK, 0.30), width=1.1)
    c.text(66, 1414, "数据落地", size=12.5, color=PINK, bold=True)
    for i, (t, v) in enumerate([
        ("照片目录", "只读 · 用户选择"),
        ("导出目录", "复制产物"),
        ("结果 JSON", "PickResult · schemaVersion=1"),
        ("settings.json", "Ark Key / 端点 / 模型"),
        ("照片 EXIF", "鸟种标签 · 不重编码"),
    ]):
        x = 190 + i * 300
        c.text(x, 1410, t, size=10.6, color=mix(PINK, TEXT, 0.45), bold=True)
        c.text(x, 1428, v, size=10.2, color=MUTED)

    x = 46
    c.text(x, 1488, "图例", size=10.5, color=DIM, bold=True)
    x += 40
    for label, color in [
        ("视图/UI", BLUE), ("交互渲染", CYAN), ("状态", AMBER), ("接入/接口", GREEN),
        ("服务与工具", BLUE), ("AI 子包", AMBER), ("运行时", PURPLE), ("契约", CYAN),
    ]:
        x += c.chip(x, 1482, label, color, size=10) + 9
    return c


# --------------------------------------------------------------------------
# D3 · API 域地图（28 个端点 / 6 个功能域）
# --------------------------------------------------------------------------
API_DOMAINS = [
    ("轮次与照片", PURPLE, "6 个端点 · 选片主循环", [
        ("GET", "/rounds/{i}/photos?offset=&limit=", "分页返回该轮照片（顺序即放映顺序）"),
        ("GET", "/photos/{pid}/image?variant=", "原图 / 缩略图字节流 · Cache-Control 1h"),
        ("POST", "/photos/{pid}/viewed", "标记本轮已浏览（幂等）"),
        ("PUT", "/photos/{pid}/pick", "保留该照片（幂等，重复不累加）"),
        ("DELETE", "/photos/{pid}/pick", "取消保留（本轮 -1 / pickCount -1）"),
        ("POST", "/rounds/next", "结束本轮 → 下一轮（要求本轮 pick ≥ 2）"),
    ]),
    ("识鸟任务", CYAN, "6 个端点 · 内存队列 + 后台 worker", [
        ("POST", "/bird-tasks", "提交识别任务 → 201 queued（立即返回）"),
        ("GET", "/bird-tasks", "任务列表 + queue 计数（前端 1s 轮询）"),
        ("GET", "/bird-tasks/{tid}", "单个任务快照（进度 / 结果 / 错误）"),
        ("POST", "/bird-tasks/{tid}/adopt", "采纳：写结果 JSON + EXIF 鸟种标签"),
        ("POST", "/bird-tasks/{tid}/dismiss", "忽略任务（queued 则不再执行）"),
        ("GET", "/bird-tasks/{tid}/reference", "参考图代理（内存 bytes · no-store）"),
    ]),
    ("结果与导出", AMBER, "5 个端点 · 落地与再筛选", [
        ("POST", "/finish", "结束会话并生成结果（幂等）"),
        ("GET", "/result[?download=1]", "结果 JSON；download=1 为附件下载"),
        ("POST", "/export", "结果 JSON 另存到本机路径（桌面原生另存为）"),
        ("POST", "/export-photos", "后端复制最后一轮保留照片（同名 -1/-2）"),
        ("POST", "/resume", "finished → 新一轮 picking（继续筛选）"),
    ]),
    ("会话生命周期", GREEN, "4 个端点 · 状态机入口", [
        ("POST", "/sessions", "创建会话：server 扫描目录 / browser 元数据"),
        ("POST", "/sessions/restore", "请求体即结果 JSON → 重扫目录恢复现场"),
        ("GET", "/sessions/current", "CLI --dir 预扫描后的当前会话"),
        ("GET", "/sessions/{sid}", "会话摘要：轮次 / 计数 / phase"),
    ]),
    ("识鸟配置", PINK, "3 个端点 · 本机凭据", [
        ("GET", "/bird/status", "是否已配置 / 掩码 Key / 端点协议 / 队列"),
        ("PUT", "/bird/config", "保存 Key / 完整端点 / 模型（缺省=不变）"),
        ("POST", "/bird/verify", "极小请求测试连接（失败也 200 + ok=false）"),
    ]),
    ("系统与兼容", BLUE, "4 个端点 · 探针与旧目录浏览", [
        ("GET", "/health", "存活探针（桌面端就绪轮询）"),
        ("GET", "/fs/roots", "盘符列表（第 2 版目录浏览，UI 已不用）"),
        ("GET", "/fs/list?path=", "列出子目录（兼容 / 调试用）"),
        ("POST", "/dialog/pick-directory", "本机原生目录框（浏览器模式兜底）"),
    ]),
]


def d3_api_map() -> Canvas:
    c = Canvas(1800, 1040)
    c.header(
        "API 域地图：28 个 REST 端点 / 6 个功能域",
        "所有端点挂在同一个 FastAPI 应用上（/api 前缀）——这是「模块化单体」，不是微服务；无鉴权、无网关、无服务发现",
        kicker="PICTUREPICK · D3 / API SURFACE",
    )

    # 顶部摘要条
    c.rrect(46, 146, 1708, 56, r=10, fill=mix(PANEL, CYAN, 0.08), outline=mix(BORDER, CYAN, 0.30), width=1.1)
    items = [
        ("28", "REST 端点", CYAN), ("6", "功能域", BLUE), ("2", "静态/SPA 路由", MUTED),
        ("0", "外部鉴权", GREEN), ("1", "出网依赖（可选）", AMBER),
    ]
    x = 70
    for num, label, color in items:
        c.text(x, 158, num, size=21, color=color, bold=True)
        c.text(x + c.tw(num, size=21, bold=True) + 7, 168, label, size=11, color=MUTED, anchor="lt")
        x += 210
    c.text(1380, 168, "路径简写：{sid}=session_id  {pid}=photo_id  {tid}=task_id  {i}=轮次下标", size=10.2, color=DIM)

    # 3 列 × 2 行
    col_w, gap = 553, 24
    rows = [(214, 6), (588, 4)]
    for r, (y0, min_rows) in enumerate(rows):
        domains = API_DOMAINS[r * 3 : r * 3 + 3]
        h_row = 52 + max(len(d[3]) for d in domains) * 48 + 10
        for i, (title, color, sub, eps) in enumerate(domains):
            x = 46 + i * (col_w + gap)
            h = h_row
            c.rrect(x, y0, col_w, h, r=10, fill=mix(PANEL, color, 0.05), outline=mix(BORDER, color, 0.35), width=1.2)
            c.rrect(x, y0, col_w, 3.4, r=1.7, fill=color)
            c.text(x + 16, y0 + 14, title, size=14, color=mix(color, TEXT, 0.30), bold=True)
            c.text(x + 16 + c.tw(title, size=14, bold=True) + 10, y0 + 17, sub, size=10, color=DIM)
            cy = y0 + 44
            for method, path, desc in eps:
                w = c.method_chip(x + 16, cy, method, size=9.4)
                c.text(x + 16 + w + 8, cy + 8.6, path, size=10.2, color=mix(color, TEXT, 0.45), mono=True, anchor="lm")
                c.text(x + 16 + w + 8, cy + 24, desc, size=9.6, color=MUTED)
                c.line(x + 16, cy + 42, x + col_w - 16, cy + 42, color=mix(BORDER, BG, 0.35), width=0.7, dash=(3, 4))
                cy += 48

    c.rrect(46, 934, 1708, 52, r=10, fill=mix(PANEL, AMBER, 0.07), outline=mix(BORDER, AMBER, 0.30), width=1.0)
    c.text(66, 944, "工程化提示", size=11.5, color=AMBER, bold=True)
    c.text(148, 946, "28 个端点写在同一个 api.py（430 行）里，没有按域拆 router、没有 OpenAPI 契约测试；"
                     "前端 api.ts 手工镜像这些路径，改名/改字段只能靠人肉同步 —— 这是 API 层最该先工程化的地方。",
           size=10.8, color=MUTED)
    c.footer_note("产物来源：scripts/arch_diagrams.py::d3_api_map() · 端点依据：backend/picturepick/api.py（28 个 @router）+ app.py（2 个 @app.get）· 契约：docs/API.md")
    return c


# --------------------------------------------------------------------------
# D4 · 会话/轮次状态机 + AI 任务状态机
# --------------------------------------------------------------------------
def _state_box(c: Canvas, x, y, w, h, title, sub=None, color=BLUE, fill=None):
    c.rrect(x, y, w, h, r=10, fill=fill or mix(PANEL, color, 0.14), outline=color, width=1.5)
    if sub:
        c.text(x + w / 2, y + h / 2 - 11, title, size=13.5, color=mix(color, TEXT, 0.25), bold=True, anchor="mm")
        c.text(x + w / 2, y + h / 2 + 10, sub, size=9.8, color=MUTED, anchor="mm")
    else:
        c.text(x + w / 2, y + h / 2, title, size=13.5, color=mix(color, TEXT, 0.25), bold=True, anchor="mm")


def d4_state_machines() -> Canvas:
    c = Canvas(1800, 980)
    c.header(
        "两个状态机：会话轮次（持久化到 JSON）· AI 识鸟任务（只在内存）",
        "选片主流程的状态全部由后端持有；前端只是镜像。任务队列重启即丢，已采纳结果落在结果 JSON 与照片 EXIF",
        kicker="PICTUREPICK · D4 / STATE MACHINES",
    )

    # ---- 左：会话 / 轮次 ---------------------------------------------------
    c.panel(46, 150, 1032, 760, "会话与轮次状态机（SessionStore · 24h TTL · 单进程内存）", color=BLUE)
    c.text(66, 200, "phase 只有两个值：picking / finished；轮次是单向累积的嵌套集合", size=10.8, color=DIM)

    _state_box(c, 96, 262, 250, 66, "无会话", "启动页 / 用户选目录", DIM, fill=mix(PANEL, BG, 0.35))
    _state_box(c, 466, 262, 240, 66, "picking", "选片中 · roundIndex ≥ 1", BLUE)
    _state_box(c, 466, 470, 240, 66, "finished", "已结束 · 可导出 / 可继续", AMBER)
    _state_box(c, 96, 470, 250, 66, "restore 恢复现场", "POST /api/sessions/restore", GREEN)

    c.arrow([(346, 295), (466, 295)], color=mix(GREEN, BORDER, 0.2), label="POST /api/sessions", label_size=9.4,
            label_at=(406, 295))
    c.arrow([(221, 470), (221, 400), (466, 400), (466, 328)], color=mix(GREEN, BORDER, 0.2))
    c.text(240, 384, "phase=\"picking\" → 回到同一轮", size=9.2, color=GREEN)
    c.arrow([(346, 503), (466, 503)], color=mix(GREEN, BORDER, 0.2))
    c.text(352, 486, "phase=\"finished\"/旧 JSON", size=9.2, color=GREEN)
    c.arrow([(586, 328), (586, 470)], color=mix(AMBER, BORDER, 0.2), label="POST .../finish", label_size=9.4,
            label_at=(586, 399))
    c.arrow([(520, 470), (520, 328)], color=mix(BLUE, BORDER, 0.2), label="POST .../resume", label_size=9.4,
            label_at=(520, 434))
    c.line(706, 503, 716, 503, color=mix(BORDER, CYAN, 0.4))
    c.text(720, 497, "resume：最后一轮保留照片建新一轮（需 ≥2 张）", size=9.2, color=DIM)
    # picking 自环
    c.arrow([(660, 262), (660, 214), (540, 214), (540, 262)], color=mix(BLUE, BORDER, 0.2))
    c.text(672, 212, "POST .../rounds/next", size=9.4, color=BLUE)
    c.text(672, 228, "本轮 pick ≥ 2 → 新一轮（上一轮 pick 的照片）", size=9.0, color=DIM)

    # 轮次嵌套示意
    c.text(66, 570, "轮次嵌套：每轮只在上一轮 pick 的照片里继续，pickCount 跨轮累计", size=10.8, color=MUTED)
    bars = [(96, 620, 620, "第 1 轮 · 全部照片 120 张", "viewed / pick 从 0 开始", BLUE),
            (96, 664, 300, "第 2 轮 · 12 张", "仅第 1 轮被 pick 的", PURPLE),
            (96, 708, 130, "第 3 轮 · 3 张", "仅第 2 轮被 pick 的", AMBER)]
    for x, y, w, t, sub, color in bars:
        c.rrect(x, y, w, 30, r=6, fill=mix(PANEL, color, 0.18), outline=mix(BORDER, color, 0.45), width=1.0)
        c.text(x + 12, y + 15, t, size=10.4, color=mix(color, TEXT, 0.35), bold=True, anchor="lm")
        c.text(x + w + 14, y + 15, sub, size=9.6, color=DIM, anchor="lm")

    c.rrect(96, 758, 952, 40, r=8, fill=mix(PANEL, GREEN, 0.06), outline=mix(BORDER, GREEN, 0.25), width=0.9)
    c.text(110, 768, "pickCount = Σ 各轮 pick；pickedInRounds = 具体轮次；lastRound.pickedPhotoIds = 导出依据",
           size=10.2, color=mix(GREEN, TEXT, 0.42))
    c.text(96, 812, "持久化边界：会话态在内存（重启丢），只有结果 JSON（phase=picking|finished）能跨进程恢复",
           size=10.4, color=DIM)
    c.text(96, 836, "恢复是「重扫源目录 + 按 relativePath 匹配」，源目录不可访问时回退 photos[].absolutePath",
           size=10.4, color=DIM)

    # ---- 右：AI 识鸟任务 ---------------------------------------------------
    c.panel(1100, 150, 654, 760, "AI 识鸟任务状态机（BirdTaskManager · 单线程 worker）", color=AMBER)
    c.text(1120, 200, "全局一次只识别一张（跨会话串行）· 任务只在内存", size=10.4, color=DIM)

    bx, bw = 1150, 200
    _state_box(c, bx, 240, bw, 58, "queued", "排队 · queuePosition ≥ 1", MUTED, fill=mix(PANEL, BG, 0.30))
    _state_box(c, bx, 336, bw, 58, "running", "识别中 · progress 单调递增", AMBER)
    _state_box(c, bx, 432, bw, 58, "completed", "有 result / referenceUrl", GREEN)
    _state_box(c, bx, 528, bw, 58, "adopted", "写入 JSON + EXIF", PINK)
    _state_box(c, 1450, 336, 180, 58, "failed", "BIRD_API_ERROR …", RED)
    _state_box(c, 1450, 432, 180, 58, "dismissed", "忽略（终态）", DIM)

    c.arrow([(1250, 298), (1250, 336)], color=mix(AMBER, BORDER, 0.2), label="worker 取出", label_size=9.2,
            label_at=(1250, 317))
    c.arrow([(1250, 394), (1250, 432)], color=mix(GREEN, BORDER, 0.2), label="识别 + 参考图", label_size=9.2,
            label_at=(1250, 413))
    c.arrow([(1250, 490), (1250, 528)], color=mix(PINK, BORDER, 0.2), label="用户采纳", label_size=9.2,
            label_at=(1250, 509))
    c.arrow([(1350, 365), (1450, 365)], color=mix(RED, BORDER, 0.2))
    c.text(1372, 346, "异常", size=9.2, color=mix(RED, MUTED, 0.3))
    c.arrow([(1350, 461), (1450, 461)], color=mix(DIM, BORDER, 0.2))
    c.arrow([(1540, 394), (1540, 412), (1415, 412), (1415, 269), (1350, 269)], color=mix(BORDER, RED, 0.35), dash=(5, 4))
    c.text(1150, 592, "failed 的重试 = 对同一 photoId 新建任务（旧任务留作历史）", size=9.4, color=DIM)
    c.text(1150, 608, "dismissed 可从 queued / running / completed / failed 任一非终态进入", size=9.4, color=DIM)

    # 进度阶梯
    c.text(1120, 632, "stage / progress 语义", size=10.6, color=AMBER, bold=True)
    stages = [("queued", "0%", MUTED), ("preparing", "10%", BLUE), ("identifying", "20→85%", AMBER),
              ("reference", "90%", PINK), ("done", "100%", GREEN)]
    x = 1120
    for name, pct, color in stages:
        w = c.chip(x, 650, f"{name} {pct}", color, size=9.6) + 8
        x += w
        if x < 1640:
            c.text(x - 4, 654, "›", size=09.6, color=DIM)
    c.text(1120, 690, "identifying 按已用时间向 85% 插值（估算 15s）· 失败保留最后 stage", size=9.6, color=MUTED)
    c.rrect(1120, 720, 614, 74, r=8, fill=mix(PANEL, RED, 0.06), outline=mix(BORDER, RED, 0.22), width=0.9)
    c.text(1134, 730, "任务级错误码（不改 HTTP 状态）", size=10.2, color=RED, bold=True)
    for i, code in enumerate(["BIRD_API_ERROR", "BIRD_API_TIMEOUT", "BIRD_PARSE_ERROR", "SESSION_NOT_FOUND", "PHOTO_NOT_FOUND"]):
        cx = 1134 + i * 120
        c.text(cx, 750, code, size=8.6, color=mix(RED, MUTED, 0.45), mono=True)
    c.text(1120, 812, "HTTP 层错误码：INVALID_CONFIG 400 / BIRD_NOT_CONFIGURED 409 / BIRD_TASK_* 404·409", size=9.6, color=DIM)
    c.text(1120, 834, "队列重启即丢：未采纳的结果不会自动恢复（已采纳已落 JSON/EXIF）", size=9.6, color=DIM)

    c.footer_note("产物来源：scripts/arch_diagrams.py::d4_state_machines() · 依据：backend/picturepick/session.py / birdid/tasks.py / docs/API.md §1·§4·§9")
    return c


# --------------------------------------------------------------------------
# D5 · AI 识鸟端到端时序（提交 → 后台识别 → 轮询采纳 → 落库）
# --------------------------------------------------------------------------
def d5_bird_sequence() -> Canvas:
    c = Canvas(1800, 1240)
    c.header(
        "AI 识鸟端到端时序：为什么选片不卡、结果从哪来、落到了哪里",
        "提交后立即 201 返回；识别在进程内单线程 worker 里跑；前端 1s 轮询；只有「采纳」才写 JSON 与照片 EXIF",
        kicker="PICTUREPICK · D5 / BIRD-ID SEQUENCE",
    )

    lanes = [
        ("选片页（前端）", BLUE), ("api.py", GREEN), ("BirdService", GREEN), ("worker 线程", AMBER),
        ("client 压图/编码", CYAN), ("ArkBirdClient", AMBER), ("reference", PINK), ("exif / Session", PINK),
    ]
    xs = [200 + i * 200 for i in range(8)]
    top = 176

    # 阶段背景带（先画，避免覆盖箭头）
    for y1, y2, label, color in [
        (246, 420, "① 提交 · 立即返回", CYAN),
        (428, 830, "② 后台识别 · 单线程串行", AMBER),
        (834, 1100, "③ 轮询 → 采纳 → 落地", PINK),
    ]:
        c.rrect(48, y1, 1704, y2 - y1, r=8, fill=mix(BG, color, 0.05), outline=mix(BORDER, BG, 0.22), width=0.8)
        c.rrect(48, y1, 4, y2 - y1, r=2, fill=color)
        c.text(62, y1 + 8, label, size=10, color=mix(color, TEXT, 0.4), bold=True)

    for (name, color), x in zip(lanes, xs):
        w = max(150, c.tw(name, size=11.4, bold=True) + 26)
        c.rrect(x - w / 2, top, w, 34, r=8, fill=mix(PANEL, color, 0.16), outline=mix(BORDER, color, 0.5), width=1.1)
        c.text(x, top + 17, name, size=11.4, color=mix(color, TEXT, 0.3), bold=True, anchor="mm")
        c.line(x, top + 34, x, 1104, color=mix(BORDER, MUTED, 0.25), width=1.0, dash=(4, 6))

    steps = [
        (268, 0, 1, "POST /api/sessions/{sid}/bird-tasks {photoId}", "solid", CYAN),
        (314, 1, 2, "submit(sid, photoId)：校验会话/照片/配置", "solid", GREEN),
        (360, 2, 3, "queue.put(taskId) · 惰性启动 daemon 线程", "solid", AMBER),
        (404, 2, 0, "201 BirdTask{status:queued, queuePosition}", "dashed", CYAN),
        (450, 3, 4, "read + exif_transpose → 长边 ≤1568 / q88 → base64", "solid", CYAN),
        (494, 4, 3, "base64 data URL", "dashed", CYAN),
        (540, 3, 5, "identify(image, resolvedConfig)", "solid", AMBER),
        (586, 5, 7, "POST 完整端点：Bearer Key + tools:[web_search]（仅 Responses）", "solid", AMBER),
        (630, 7, 5, "{choices[0].message.content | output[].content}", "dashed", AMBER),
        (674, 5, 3, "BirdResult（parser 容错 JSON 解析 / 清洗）", "dashed", GREEN),
        (720, 3, 6, "resolve(referenceImageUrl)", "solid", PINK),
        (764, 6, 3, "SSRF 校验 → 下载 → 缩放 → bytes（只存内存）", "dashed", PINK),
        (810, 3, 2, "task = completed · progress=100%", "solid", GREEN),
        (856, 0, 1, "GET .../bird-tasks（每 1000ms 轮询，隐藏页暂停）", "solid", BLUE),
        (902, 0, 1, "POST .../{tid}/adopt（用户点「采纳」）", "solid", PINK),
        (946, 1, 2, "adopt(sid, tid)", "solid", GREEN),
        (992, 2, 7, "write_bird_exif() + 记录 BirdObservation", "solid", PINK),
        (1036, 2, 1, "BirdAdoptResponse{observation, exif}", "dashed", GREEN),
        (1080, 1, 0, "200 + 结果 JSON 已含 photos[].bird", "dashed", CYAN),
    ]
    for y, src, dst, label, style, color in steps:
        x1, x2 = xs[src], xs[dst]
        dirn = 1 if x2 > x1 else -1
        c.arrow([(x1 + 4 * dirn, y), (x2 - 4 * dirn, y)],
                color=mix(color, BORDER, 0.15), width=1.6,
                dash=(6, 5) if style == "dashed" else None)
        tx = x1 + 14 * dirn
        c.text(tx, y - 13, label, size=9.6, color=mix(color, MUTED, 0.35), mono=is_ascii(label),
               anchor="lm" if dirn > 0 else "rm")

    # 底部说明
    c.rrect(48, 1112, 1704, 40, r=8, fill=mix(PANEL, AMBER, 0.06), outline=mix(BORDER, AMBER, 0.25), width=0.9)
    c.text(62, 1122, "隐私边界：只有被识别的这一张照片会压到 ≤1568px 上传方舟；参考图 URL 由模型给出，后端仅做 SSRF 校验与下载，不搜索、不生成、不落盘。",
           size=10.4, color=mix(AMBER, TEXT, 0.35))
    c.text(62, 1162, "注意：压图/编码在 birdid/client.py::prepare_image_bytes 内完成；imaging.py 只服务前端 /image 出图（原图与缩略图），不参与识鸟。", size=9.8, color=DIM)
    c.text(62, 1184, "失败路径：超时 90s / 429 / 5xx → 重试一次；401/403/404 → failed 且不回退；ToolNotOpen → 去掉 tools 重试（识鸟仍成功，参考图缺失）；任何步骤失败都不写 EXIF。",
           size=10.2, color=DIM)
    c.footer_note("产物来源：scripts/arch_diagrams.py::d5_bird_sequence() · 依据：docs/ARCHITECTURE.md §4.5/§6 · backend/picturepick/birdid/{service,tasks,client,reference,exif}.py")
    return c


# --------------------------------------------------------------------------
# D6 · 工程化路线图（vibe code → engineered）
# --------------------------------------------------------------------------
def d6_roadmap() -> Canvas:
    c = Canvas(1800, 1290)
    c.header(
        "工程化路线图：从「能跑的 vibe code」到「可交付的工程」",
        "6 条主线 W1–W6 与 docs/ENGINEERING-ROADMAP.md 任务编号一一对应；P0 先铺地基（不改业务行为），P1 加门禁与解耦，P2 做架构与交付升级",
        kicker="PICTUREPICK · D6 / ENGINEERING ROADMAP",
    )

    # 现状 / 目标
    c.rrect(46, 146, 834, 132, r=10, fill=mix(PANEL, RED, 0.07), outline=mix(BORDER, RED, 0.30), width=1.1)
    c.text(62, 158, "现状 · vibe code 的真实形态", size=12.6, color=RED, bold=True)
    c.bullets(62, 184, [
        "无 CI、无 lint、无前端测试；验证靠本机脚本与人工",
        "430 行 api.py / 985 行 picker.ts / 3880 行 styles.css 单文件",
        "契约靠手写 docs/API.md 与手抄 types.ts 同步",
        "会话与任务全在内存，重启即丢；无覆盖率与质量门槛",
    ], 790, size=10.4, color=MUTED, dot_color=RED)

    c.rrect(900, 146, 854, 132, r=10, fill=mix(PANEL, GREEN, 0.07), outline=mix(BORDER, GREEN, 0.30), width=1.1)
    c.text(916, 158, "目标 · 「一条命令 = 一次 CI」", size=12.6, color=GREEN, bold=True)
    c.bullets(916, 184, [
        "scripts/ci.ps1：lint + type + test + build 一键复现，CI 只是调用它",
        "API 按域拆 router，前端类型由 OpenAPI 生成（手抄消失）",
        "分层 e2e（mock / 真实后端 / 真 Key 隔离），失败可定位",
        "版本单一来源 + CHANGELOG + 可复现打包 + 会话可恢复",
    ], 810, size=10.4, color=MUTED, dot_color=GREEN)

    # 泳道网格
    lanes = [
        ("W1 版本控制\n与仓库卫生", BLUE, [
            ["提交在途交付物（12 M + 11 ??，含本次架构文档）", ".gitignore 只缺 .pytest-run/（其余已被忽略）"],
            ["PR 模板 + Conventional Commits + 分支保护", "提交信息 / scope 校验进 CI"],
            ["docs/DEV-SETUP.md：clone → 跑通 ≤15 分钟", "清理本地 4.5GB 构建缓存"],
        ]),
        ("W2 依赖与构建\n可复现", CYAN, [
            ["requirements.txt 全 == + 拆 requirements-dev.txt", "build.ps1:94/111 与 dev.ps1:32 改 npm ci"],
            ["pip-tools/uv 生成 requirements.lock", "package.json 声明 engines + CI 校验 lock"],
            ["固定 Python/Node/PyInstaller 版本", "产物 sha256 + manifest，两次构建一致"],
        ]),
        ("W3 CI 与质量门禁\nLint·Type", GREEN, [
            ["scripts/ci.ps1：pytest + tsc×2 + build", ".github/workflows/ci.yml 三 job（windows-latest）"],
            ["ruff + eslint + prettier 只 check 不改", "mypy 白名单（models/api/session）"],
            ["pytest-cov 覆盖率门槛 ≥70%", "覆盖率报告与回退拦截进 CI"],
        ]),
        ("W4 测试工程\nUnit·E2E", AMBER, [
            ["e2e 参数化：端口 / EXPECTED_TOTAL / 截图落 .tmp", "连续两次运行 PASS 且工作树无新增"],
            ["Vitest 覆盖 api 错误映射 / viewer 数学 / 轮询", "e2e 三层隔离：mock / 真后端 / 真 Key"],
            ["OpenAPI 快照 + 生成类型纳入 tsc", "关键端点响应字段快照测试"],
        ]),
        ("W5 契约先行\n与架构解耦", PINK, [
            ["OpenAPI 快照（/api/openapi.json 已存在）", "快照 diff 即测试失败，不允许悄悄改契约"],
            ["openapi-typescript 生成 types.gen.ts", "api.py 按 6 域拆 router，仍 28 条路由"],
            ["picker.ts 拆 view/controller/快捷键/放映", "胶片条与结果表虚拟化（支撑 5 万张）"],
        ]),
        ("W6 可观测·安全\n与发布", PURPLE, [
            ["版本号单一来源校验脚本（收敛 11 处）", "后端 logging + Key/路径脱敏替代 print"],
            ["Electron sandbox:true + CSP 落地", "SSRF 加固：DNS 解析 / 重定向复核 / 读取上限"],
            ["会话与任务持久化（SQLite），重启可续", "CHANGELOG + tag 触发跨平台发布"],
        ]),
    ]
    phases = [("P0 · 0–2 周「先把地基铺平」", "不改业务行为，只让一切可重复"),
              ("P1 · 2–6 周「门禁与解耦」", "让错误在合并前暴露"),
              ("P2 · 6–12 周「架构与交付」", "让架构与发布可持续")]
    col_x = [340, 810, 1290, 1740]
    y0 = 300
    for i, (title, sub) in enumerate(phases):
        x = col_x[i]
        c.rrect(x + 4, y0, col_x[i + 1] - x - 12, 52, r=8, fill=mix(PANEL, [BLUE, CYAN, PURPLE][i], 0.12),
                outline=mix(BORDER, [BLUE, CYAN, PURPLE][i], 0.35), width=1.0)
        c.text(x + 16, y0 + 8, title, size=11.8, color=mix([BLUE, CYAN, PURPLE][i], TEXT, 0.3), bold=True)
        c.text(x + 16, y0 + 30, sub, size=9.6, color=DIM)

    row_h = 128
    for r, (lane_label, color, cells) in enumerate(lanes):
        ry = y0 + 60 + r * row_h
        c.rrect(46, ry, 280, row_h - 10, r=9, fill=mix(PANEL, color, 0.10), outline=mix(BORDER, color, 0.38), width=1.0)
        c.rrect(46, ry, 4, row_h - 10, r=2, fill=color)
        lines = lane_label.split("\n")
        c.text(66, ry + row_h / 2 - 10, lines[0], size=12.4, color=mix(color, TEXT, 0.28), bold=True, anchor="lm")
        if len(lines) > 1:
            c.text(66, ry + row_h / 2 + 9, lines[1], size=9.8, color=DIM, anchor="lm")
        for i, items in enumerate(cells):
            x = col_x[i] + 4
            w = col_x[i + 1] - x - 12
            iy = ry + 4
            for item in items:
                nlines = len(c.wrap(item, w - 28, size=9.6))
                h = 8 + nlines * 13 + 8
                c.rrect(x, iy, w, h, r=6, fill=mix(PANEL_HI, color, 0.05), outline=mix(BORDER, color, 0.20), width=0.8)
                c.circle(x + 12, iy + 11, 2.2, fill=color)
                c.para(x + 20, iy + 5, item, w - 28, size=9.6, color=MUTED, line_h=1.4)
                iy += h + 5

    # 度量条
    c.rrect(46, 1146, 1708, 56, r=10, fill=mix(PANEL, GREEN, 0.06), outline=mix(BORDER, GREEN, 0.28), width=1.0)
    c.text(62, 1162, "验收度量", size=11.4, color=GREEN, bold=True)
    metrics = ["ci.ps1 一条命令全绿", "ruff/mypy/tsc 0 error", "pytest ≥ 292 且覆盖率 ≥ 70%",
               "前端单测覆盖核心纯逻辑", "OpenAPI diff 必须显式", "打包 3 平台可复现"]
    x = 160
    for m in metrics:
        x += c.chip(x, 1160, m, GREEN, size=9.8) + 8

    c.footer_note("产物来源：scripts/arch_diagrams.py::d6_roadmap() · 依据：.scratch/arch/audit.md（工程化审计）+ backend/tests 规模 + frontend/desktop 结构；周期为建议排期，可按人力缩放")
    return c


# --------------------------------------------------------------------------
# 产物落盘
# --------------------------------------------------------------------------
DIAGRAMS = {
    "d1-context": ("PicturePick-D1-系统上下文与部署拓扑.png", d1_context),
    "d2-layers": ("PicturePick-D2-分层组件架构.png", d2_layers),
    "d3-api": ("PicturePick-D3-API域地图.png", d3_api_map),
    "d4-states": ("PicturePick-D4-状态机.png", d4_state_machines),
    "d5-bird-seq": ("PicturePick-D5-AI识鸟时序.png", d5_bird_sequence),
    "d6-roadmap": ("PicturePick-D6-工程化路线图.png", d6_roadmap),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 PicturePick 架构图")
    parser.add_argument("--only", action="append", default=None, help="只生成指定图（d1-context / d2-layers ...）")
    parser.add_argument("--out", default="docs/architecture/diagrams", help="输出目录（相对仓库根）")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    out_dir = (root / args.out).resolve()
    wanted = args.only or list(DIAGRAMS)
    for key in wanted:
        if key not in DIAGRAMS:
            print(f"未知图 id: {key}（可选 {', '.join(DIAGRAMS)}）")
            return 2
        filename, factory = DIAGRAMS[key]
        canvas = factory()
        path = canvas.save(out_dir / filename)
        print(f"[ok] {key:12s} {path.relative_to(root)}  {canvas.w}x{canvas.h} logical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
