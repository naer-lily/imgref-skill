"""编号拼图的渲染。

尺寸不是随便定的：默认 4×3、格子 384，得到 1572×1216，**正好卡在视觉模型
1536 长边预算附近**。再大就会被调用方降采样，每格糊成 250px，纯属浪费算力。

编号画在每格**底部的黑条**上（不是角落徽章）：底部条不会盖住主体，
字号可以开到很大，视觉模型读数字几乎不出错。JPEG 用 ``subsampling=0``
保证文字边缘不糊。

图上的文字**只用 ASCII**：Pillow 默认字体渲染不了中文，为了中文标签去背一个
CJK 字体又会把"零字体依赖"这个优点丢掉。中文说明放在 stdout 表和 JSON 里。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

__all__ = ["GridCell", "GridSize", "GridSpec", "ascii_only", "ascii_title", "render_grid"]

_WHITE: Final = (255, 255, 255)
_CAPTION_BG: Final = (17, 17, 17)
_BORDER: Final = (204, 204, 204)
_NOTE_FG: Final = (188, 188, 188)
_PLACEHOLDER_BG: Final = (238, 238, 238)
_PLACEHOLDER_FG: Final = (150, 150, 150)


@dataclass(frozen=True, slots=True)
class GridSpec:
    """拼图版式。

    默认值不是随手取的：``4×3`` 格、每格 375px、编号条 41px、间距 8px、边距 6px
    → 画布恰好 ``1536×1310``，正好顶满视觉模型的 1536 长边预算而不浪费。

    Attributes:
        cols: 列数。
        rows: 行数。
        cell: 每格图片边长（正方形，等比适配后留白）。
        gap: 格子间距。
        pad: 画布外边距。
        header: 顶部标题栏高度。
        caption: 每格底部编号条高度。
        quality: JPEG 质量。
    """

    cols: int = 4
    rows: int = 3
    cell: int = 375
    gap: int = 8
    pad: int = 6
    header: int = 34
    caption: int = 41
    quality: int = 88

    @property
    def capacity(self) -> int:
        """这张图最多放几张。"""
        return self.cols * self.rows

    @property
    def width(self) -> int:
        """画布宽度（像素）。"""
        return self.cols * self.cell + (self.cols - 1) * self.gap + 2 * self.pad

    @property
    def height(self) -> int:
        """画布高度（像素）。"""
        row = self.cell + self.caption
        return self.header + self.rows * row + (self.rows - 1) * self.gap + 2 * self.pad

    def for_cell(self, cell: int) -> GridSpec:
        """换一个格子尺寸，并按比例调整标题栏与编号条。"""
        cell = max(cell, 64)
        return replace(self, cell=cell, caption=max(18, round(cell * 0.11)), header=max(22, round(cell * 0.09)))

    def fit_max_edge(self, max_edge: int) -> GridSpec:
        """在长边不超过 ``max_edge`` 的前提下收敛格子尺寸。"""
        if max_edge <= 0 or self.width <= max_edge:
            return self
        available = max_edge - 2 * self.pad - (self.cols - 1) * self.gap
        return self.for_cell(max(available // self.cols, 64))


@dataclass(frozen=True, slots=True)
class GridCell:
    """拼图里的一格。

    Attributes:
        label: 底部条上的大字（序号）。
        data: 图片字节；``None`` 渲染成占位格。
        note: 底部条上的小字补充（只能用 ASCII）。
    """

    label: str
    data: bytes | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class GridSize:
    """渲染结果。"""

    width: int
    height: int
    rendered: int


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """取 Pillow 自带的可缩放默认字体（不需要任何字体文件）。"""
    return ImageFont.load_default(size=max(size, 8))


def _fit(data: bytes, size: int) -> Image.Image:
    """把图片等比缩放到 ``size``×``size`` 并居中留白。"""
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")
    return ImageOps.pad(image, (size, size), method=Image.Resampling.LANCZOS, color=_WHITE, centering=(0.5, 0.5))


def render_grid(path: Path, cells: list[GridCell] | tuple[GridCell, ...], spec: GridSpec, title: str = "") -> GridSize:
    """渲染编号拼图并写盘。

    Args:
        path: 输出文件路径（JPEG）。
        cells: 格子内容，超出 ``spec.capacity`` 的部分被忽略。
        spec: 版式。
        title: 顶部标题（ASCII）。

    Returns:
        实际画布尺寸与实际渲染的格子数。
    """
    used = list(cells)[: spec.capacity]
    canvas = Image.new("RGB", (spec.width, spec.height), _WHITE)
    draw = ImageDraw.Draw(canvas)
    if title:
        draw.text((spec.pad + 6, spec.header / 2), title, anchor="lm", fill=(40, 40, 40), font=_font(int(spec.header * 0.58)))
    label_font = _font(int(spec.caption * 0.78))
    note_font = _font(max(int(spec.caption * 0.44), 10))
    placeholder_font = _font(max(int(spec.cell * 0.07), 12))
    for index in range(spec.capacity):
        col = index % spec.cols
        row = index // spec.cols
        x = spec.pad + col * (spec.cell + spec.gap)
        y = spec.header + spec.pad + row * (spec.cell + spec.caption + spec.gap)
        cell = used[index] if index < len(used) else None
        if cell is None:
            # 候选数少于格子数：留白 + 细边框就够了，不画编号条
            # （否则半张图都是黑条，看着像坏了）
            draw.rectangle((x, y, x + spec.cell, y + spec.cell), outline=_BORDER)
            continue
        if cell.data:
            try:
                canvas.paste(_fit(cell.data, spec.cell), (x, y))
            except (UnidentifiedImageError, OSError, ValueError):
                _draw_placeholder(draw, x, y, spec, placeholder_font)
        else:
            _draw_placeholder(draw, x, y, spec, placeholder_font)
        caption_top = y + spec.cell
        draw.rectangle((x, caption_top, x + spec.cell, caption_top + spec.caption), fill=_CAPTION_BG)
        middle = caption_top + spec.caption / 2
        draw.text((x + 11, middle), cell.label, anchor="lm", fill=_WHITE, font=label_font)
        if cell.note:
            offset = draw.textlength(cell.label, font=label_font)
            draw.text((x + 11 + offset + 10, middle + 2), cell.note[:22], anchor="lm", fill=_NOTE_FG, font=note_font)
        draw.rectangle((x, y, x + spec.cell, caption_top + spec.caption), outline=_BORDER)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, format="JPEG", quality=spec.quality, optimize=True, subsampling=0)
    return GridSize(width=spec.width, height=spec.height, rendered=len(used))


def _draw_placeholder(draw: ImageDraw.ImageDraw, x: int, y: int, spec: GridSpec, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> None:
    """画一个"没有图"的占位格。"""
    draw.rectangle((x, y, x + spec.cell, y + spec.cell), fill=_PLACEHOLDER_BG)
    draw.text((x + spec.cell / 2, y + spec.cell / 2), "n/a", anchor="mm", fill=_PLACEHOLDER_FG, font=font)


def ascii_only(text: str) -> str:
    """只保留可打印 ASCII。"""
    return "".join(ch for ch in text if 32 <= ord(ch) < 127).strip()


def ascii_title(*parts: str | None) -> str:
    """把若干片段拼成 ASCII 标题。

    非 ASCII 字符直接丢掉——所以 ``"M1911各角度图片"`` 会留下 ``M1911``，
    这正好是想要的效果；中文说明走 stdout 表和 JSON，不往图上画。
    """
    chunks = [cleaned for part in parts if part for cleaned in (ascii_only(part)[:34],) if cleaned]
    return " | ".join(chunks)
