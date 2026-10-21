"""Render a code-native, non-device H3 force-field visual preview.

The drawing deliberately contains no device I/O, calibration, or firmware logic.
It is a visual prototype for a future QPainter scene, not a measurement report.

Run with a Python that has Pillow:
    python tools/h3_2p5d_preview.py
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT, SCALE = 1500, 900, 3
ROOT = Path(__file__).resolve().parents[1]
FONT_REGULAR = Path("C:/Windows/Fonts/msyh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")
FONT_NUMBER = Path("C:/Windows/Fonts/seguisb.ttf")

BG = "#071419"
SURFACE = "#0D2026"
SURFACE_LINE = "#27444B"
SCENE = "#091A1F"
BOARD_TOP = "#253940"
BOARD_SIDE = "#13252B"
PAD = "#1C3036"
PAD_ACTIVE = "#254147"
PAD_LINE = "#49646A"
PAD_LINE_IDLE = "#365157"
TEXT = "#EDF5F4"
SECONDARY = "#9BB8BE"
MUTED = "#718E96"
CYAN = "#6ADBD5"
CYAN_DARK = "#124A50"
AMBER = "#F2C46D"
AMBER_DARK = "#725328"


def xy(point):
    return tuple(round(value * SCALE) for value in point)


def box(rect):
    return tuple(round(value * SCALE) for value in rect)


@lru_cache(maxsize=None)
def font(size, bold=False, numeric=False):
    path = FONT_NUMBER if numeric else FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), round(size * SCALE))


class Canvas:
    def __init__(self):
        self.image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BG)
        self.draw = ImageDraw.Draw(self.image)

    def rounded(self, bounds, radius, fill, outline=None, width=1):
        self.draw.rounded_rectangle(
            box(bounds), round(radius * SCALE), fill=fill, outline=outline,
            width=round(width * SCALE),
        )

    def line(self, points, fill, width=1, joint="curve"):
        self.draw.line([xy(p) for p in points], fill=fill, width=round(width * SCALE), joint=joint)

    def polygon(self, points, fill, outline=None, width=1):
        vertices = [xy(p) for p in points]
        self.draw.polygon(vertices, fill=fill)
        if outline:
            self.draw.line(vertices + vertices[:1], fill=outline, width=round(width * SCALE), joint="curve")

    def ellipse(self, bounds, fill, outline=None, width=1):
        self.draw.ellipse(box(bounds), fill=fill, outline=outline, width=round(width * SCALE))

    def text(self, x, y, value, size, fill=TEXT, bold=False, anchor="lt", numeric=False):
        self.draw.text(xy((x, y)), value, font=font(size, bold, numeric), fill=fill, anchor=anchor)

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(path)


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def mul(a, n):
    return (a[0] * n, a[1] * n)


def at(origin, u, v, col, row):
    return add(origin, add(mul(u, col), mul(v, row)))


def chamfered_cell(origin, u, v, col, row, inset=0.08, cut=0.065):
    a = at(origin, u, v, col + inset, row + inset)
    b = at(origin, u, v, col + 1 - inset, row + inset)
    c = at(origin, u, v, col + 1 - inset, row + 1 - inset)
    d = at(origin, u, v, col + inset, row + 1 - inset)
    corners = (a, b, c, d)
    vertices = []
    for index, corner in enumerate(corners):
        previous = corners[index - 1]
        following = corners[(index + 1) % len(corners)]
        vertices.append(add(corner, mul((previous[0] - corner[0], previous[1] - corner[1]), cut)))
        vertices.append(add(corner, mul((following[0] - corner[0], following[1] - corner[1]), cut)))
    return vertices


def draw_arrow(canvas, cx, cy, load, is_peak):
    if load <= 0:
        canvas.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), PAD_LINE_IDLE, outline=MUTED)
        return

    accent = AMBER if is_peak else CYAN
    dark = AMBER_DARK if is_peak else CYAN_DARK
    # Flat contact datum: no floating sphere, halo, or trophy-like socket.
    canvas.line([(cx - 18, cy + 2), (cx + 18, cy + 2)], accent, width=2)
    canvas.ellipse((cx - 5, cy - 3, cx + 5, cy + 7), accent)

    if load < 0.5:
        canvas.ellipse((cx - 10, cy - 23, cx + 10, cy - 3), accent, outline=dark, width=2)
        return

    # Deliberate, screen-space vector silhouette. Its baseline is the contact pad.
    # 0..8 N uses one fixed scale; exact readings remain in text at the right.
    height = 22 + min(load, 8.0) * 17.0
    head_h = min(26.0, max(13.0, height * 0.26))
    head_half = 17 if is_peak else 15
    shaft_half = 6 if is_peak else 5
    top = cy - height
    shape = [
        (cx, top),
        (cx + head_half, top + head_h),
        (cx + shaft_half, top + head_h),
        (cx + shaft_half, cy - 7),
        (cx - shaft_half, cy - 7),
        (cx - shaft_half, top + head_h),
        (cx - head_half, top + head_h),
    ]
    canvas.polygon(shape, accent, outline=dark, width=1)


def draw_scene(canvas, values):
    d = canvas
    d.rounded((28, 119, 1059, 852), 15, SURFACE, outline=SURFACE_LINE)
    d.text(57, 146, "八点法向力分布", 23, bold=True)
    d.text(57, 188, "4 行 × 2 列   /   S1–S4 前区   /   S5–S8 后区", 14, SECONDARY)
    d.text(1023, 153, "Fz", 16, CYAN, bold=True, anchor="rt", numeric=True)
    d.line([(1028, 184), (1028, 207)], CYAN, width=2)
    d.polygon([(1028, 179), (1023, 188), (1033, 188)], CYAN)

    origin, u, v = (470, 330), (235, 38), (-64, 95)
    a = origin
    b = at(origin, u, v, 2, 0)
    c = at(origin, u, v, 2, 4)
    e = at(origin, u, v, 0, 4)
    depth = (0, 10)
    d.polygon([e, c, add(c, depth), add(e, depth)], BOARD_SIDE)
    d.polygon([b, c, add(c, depth), add(b, depth)], "#182A30")
    d.polygon([a, b, c, e], BOARD_TOP, outline="#647A7E", width=1)

    # One surface and one restrained outline per pad; no stacked plastic layers.
    for index, load in enumerate(values):
        row, col = divmod(index, 2)
        is_peak = load > 0 and load == max(values)
        vertices = chamfered_cell(origin, u, v, col, row)
        d.polygon(vertices, PAD_ACTIVE if load > 0 else PAD,
                  outline=AMBER if is_peak else PAD_LINE if load > 0 else PAD_LINE_IDLE,
                  width=2 if is_peak else 1)

        label = at(origin, u, v, col + 0.14, row + 0.73)
        d.text(label[0], label[1], f"S{index + 1}", 16,
               AMBER if is_peak else TEXT if load > 0 else "#B9CED0",
               bold=True)
        if load > 0:
            # A plane-bound linear bar complements the arrow when loads are small.
            start = at(origin, u, v, col + 0.58, row + 0.75)
            end = at(origin, u, v, col + 0.58 + 0.27 * min(load / 8.0, 1.0), row + 0.75)
            d.line([start, end], AMBER if is_peak else CYAN, width=4)

    # The half-boundary represents the front/rear reporting split, not hardware.
    split_a = at(origin, u, v, 0, 2)
    split_b = at(origin, u, v, 2, 2)
    d.line([split_a, split_b], "#8EAAAE", width=2)

    for index, load in enumerate(values):
        row, col = divmod(index, 2)
        center = at(origin, u, v, col + 0.5, row + 0.5)
        draw_arrow(d, *center, load, load > 0 and load == max(values))

    d.line([(57, 804), (1030, 804)], SURFACE_LINE)
    d.text(57, 819, "箭头朝向仅是 +Fz 显示轴；长度按 0–8 N 示意，不代表实际施力方向。", 13, SECONDARY)


def draw_sidebar(canvas, values):
    d = canvas
    d.rounded((1076, 119, 1472, 852), 15, SURFACE, outline=SURFACE_LINE)
    total = sum(values)
    front = sum(values[:4])
    rear = sum(values[4:])
    d.text(1103, 149, "合力", 15, SECONDARY)
    d.text(1101, 182, f"{total:0.2f}", 47, TEXT, numeric=True)
    d.text(1313, 211, "N", 19, SECONDARY, numeric=True)
    d.text(1104, 252, "模拟载荷 · 非设备实时数据", 13, MUTED)
    d.line([(1103, 288), (1443, 288)], SURFACE_LINE)
    d.text(1103, 310, "前区  S1–S4", 14, SECONDARY)
    d.text(1441, 307, f"{front:0.2f} N", 18, TEXT, anchor="rt", numeric=True)
    d.text(1103, 348, "后区  S5–S8", 14, SECONDARY)
    d.text(1441, 345, f"{rear:0.2f} N", 18, TEXT, anchor="rt", numeric=True)
    d.line([(1103, 391), (1443, 391)], SURFACE_LINE)
    d.text(1103, 412, "位置响应", 17, TEXT, bold=True)
    d.text(1103, 445, "局部 ΔB 比例分配，不是逐点标定力", 12, SECONDARY)

    for index, load in enumerate(values):
        y = 487 + index * 42
        is_peak = load > 0 and load == max(values)
        accent = AMBER if is_peak else CYAN
        d.text(1103, y, f"S{index + 1}", 14, accent if load > 0 else SECONDARY, bold=True)
        d.rounded((1148, y + 5, 1348, y + 10), 2.5, "#274046")
        if load > 0:
            d.rounded((1148, y + 5, 1148 + 200 * min(load / 8.0, 1.0), y + 10), 2.5, accent)
        d.text(1442, y - 2, f"{load:0.2f}", 15,
               accent if load > 0 else SECONDARY, anchor="rt", numeric=True)
    d.text(1103, 826, "仅显示 Fz；不暗示 Fx / Fy 已完成标定。", 12, MUTED)


def render(values, path):
    canvas = Canvas()
    d = canvas
    d.text(29, 27, "TacAI H3.0", 29, TEXT, bold=True)
    d.text(29, 73, "力场显示 / 2.5D 视觉预览", 15, SECONDARY)
    d.rounded((1321, 33, 1470, 74), 8, "#172B30", outline="#315159")
    d.text(1396, 45, "模拟数据", 13, SECONDARY, anchor="mt")
    draw_scene(d, values)
    draw_sidebar(d, values)
    d.save(path)


def main():
    target = ROOT / "docs" / "h3_forcefield_2p5d_preview.png"
    render([7.5, 0.0, 0.0, 3.0, 0.0, 0.0, 1.5, 0.0], target)
    print(target)


if __name__ == "__main__":
    main()
