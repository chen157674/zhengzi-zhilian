"""以比赛技术文档源稿生成可提交PDF，所有输出保留在本项目。"""
from pathlib import Path
from html import escape
import re
import os

OUT = Path(__file__).resolve().parents[1]
REPO = OUT
SCRATCH = REPO / ".tmp/document"
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = str(SCRATCH)

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Image, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Circle
from reportlab.pdfgen.canvas import Canvas

REGULAR = REPO / "data/fonts/noto-sans-sc/GuanDongSansSC-Regular.ttf"
BOLD = REPO / "data/fonts/noto-sans-sc/GuanDongSansSC-SemiBold.ttf"
pdfmetrics.registerFont(TTFont("GuanDong", str(REGULAR)))
pdfmetrics.registerFont(TTFont("GuanDongBold", str(BOLD)))
INK = colors.HexColor("#17292A")
GREEN = colors.HexColor("#216B57")
MUTED = colors.HexColor("#536563")
LINE = colors.HexColor("#D9E3DF")
SOFT = colors.HexColor("#F1F6F3")
WIDTH, HEIGHT = A4
CONTENT = WIDTH - 104

styles = {
    "body": ParagraphStyle(
        "body", fontName="GuanDong", fontSize=10.7, leading=18.5,
        textColor=INK, spaceAfter=9, wordWrap="CJK", alignment=TA_JUSTIFY,
    ),
    "h1": ParagraphStyle(
        "h1", fontName="GuanDongBold", fontSize=21, leading=30,
        textColor=INK, spaceAfter=18, keepWithNext=True,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="GuanDongBold", fontSize=12.5, leading=20,
        textColor=INK, spaceBefore=10, spaceAfter=7, keepWithNext=True,
    ),
    "cell": ParagraphStyle(
        "cell", fontName="GuanDong", fontSize=9.2, leading=14.2,
        textColor=INK, wordWrap="CJK",
    ),
    "headcell": ParagraphStyle(
        "headcell", fontName="GuanDongBold", fontSize=9.2, leading=14.2,
        textColor=colors.white, wordWrap="CJK",
    ),
    "caption": ParagraphStyle(
        "caption", fontName="GuanDong", fontSize=8.5, leading=13,
        textColor=MUTED, alignment=TA_CENTER, spaceBefore=6, spaceAfter=12,
    ),
    "cover": ParagraphStyle(
        "cover", fontName="GuanDongBold", fontSize=43, leading=56,
        textColor=INK, spaceAfter=15,
    ),
    "subtitle": ParagraphStyle(
        "subtitle", fontName="GuanDongBold", fontSize=17, leading=27,
        textColor=INK, spaceAfter=16,
    ),
    "meta": ParagraphStyle(
        "meta", fontName="GuanDong", fontSize=11, leading=20,
        textColor=MUTED, spaceAfter=5,
    ),
}


def para(text, style="body"):
    value = escape(text)
    value = re.sub(r"`([^`]+)`", r"<b>\1</b>", value)
    return Paragraph(value, styles[style])


def diagram(kind):
    if kind == "workflow":
        drawing = Drawing(CONTENT, 110)
        labels = ["采集画面", "连续观察", "形成结果", "定位回看", "AI解读"]
        box = 84
        gap = (CONTENT - box * 5) / 4
        for i, text in enumerate(labels):
            x = i * (box + gap)
            drawing.add(Rect(x, 47, box, 40, rx=7, fillColor=SOFT, strokeColor=LINE))
            drawing.add(String(x + box / 2, 62, text, fontName="GuanDongBold", fontSize=10, textAnchor="middle", fillColor=INK))
            if i < 4:
                drawing.add(Line(x + box, 67, x + box + gap, 67, strokeColor=GREEN, strokeWidth=1))
        drawing.add(String(CONTENT / 2, 19, "画面时间与训练证据贯穿整个工作流", fontName="GuanDong", fontSize=9, textAnchor="middle", fillColor=MUTED))
        return drawing
    drawing = Drawing(CONTENT, 235)
    layers = [
        ("浏览器呈现", "媒体采集  ·  Canvas骨架  ·  任务状态  ·  问题定位"),
        ("API与任务", "Flask  ·  视频任务  ·  实时会话  ·  权限与来源校验"),
        ("视觉与规则", "MediaPipe  ·  ONNX关键点  ·  阶段与连续观察"),
        ("数据与AI", "SQLAlchemy / SQLite  ·  训练证据  ·  流式AI解读"),
    ]
    for i, (name, detail) in enumerate(layers):
        y = 179 - i * 56
        drawing.add(Rect(0, y, CONTENT, 45, rx=7, fillColor=SOFT if i % 2 == 0 else colors.white, strokeColor=LINE))
        drawing.add(String(15, y + 18, name, fontName="GuanDongBold", fontSize=10, fillColor=INK))
        drawing.add(String(117, y + 18, detail, fontName="GuanDong", fontSize=9.4, fillColor=MUTED))
        if i < 3:
            drawing.add(Line(CONTENT / 2, y, CONTENT / 2, y - 11, strokeColor=GREEN))
    return drawing


def cover_graphic():
    drawing = Drawing(CONTENT, 155)
    joints = [(312, 135), (278, 106), (346, 106), (256, 72), (374, 77),
              (225, 100), (405, 101), (294, 44), (337, 44), (277, 4), (359, 4)]
    for a, b in [(0, 1), (0, 2), (1, 2), (1, 3), (3, 5), (2, 4), (4, 6),
                 (1, 7), (2, 8), (7, 8), (7, 9), (8, 10)]:
        x, y = joints[a]
        q, r = joints[b]
        drawing.add(Line(x, y, q, r, strokeColor=GREEN, strokeWidth=2))
    for x, y in joints:
        drawing.add(Circle(x, y, 4, fillColor=colors.white, strokeColor=GREEN, strokeWidth=1.5))
    drawing.add(String(0, 102, "看清动作", fontName="GuanDongBold", fontSize=18, fillColor=INK))
    drawing.add(String(0, 72, "理解训练", fontName="GuanDongBold", fontSize=18, fillColor=INK))
    drawing.add(String(0, 40, "视觉观察与Web交互协同", fontName="GuanDong", fontSize=10, fillColor=MUTED))
    return drawing


def render_table(lines):
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [row for row in rows if not all(re.fullmatch(r"[: -]+", cell) for cell in row)]
    count = len(rows[0])
    widths = [CONTENT * .26, CONTENT * .74] if count == 2 else [CONTENT * .22, CONTENT * .39, CONTENT * .39]
    data = [[para(cell, "headcell" if i == 0 else "cell") for cell in row] for i, row in enumerate(rows)]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


class NumberedCanvas(Canvas):
    def __init__(self, *args, **kwargs):
        Canvas.__init__(self, *args, **kwargs)
        self.states = []

    def showPage(self):
        self.states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self.states)
        for state in self.states:
            self.__dict__.update(state)
            self.setFont("GuanDong", 8)
            self.setFillColor(MUTED)
            self.drawString(52, 29, "观动知姿项目")
            self.drawRightString(WIDTH - 52, 29, f"{self._pageNumber} / {total}")
            if self._pageNumber > 1:
                self.drawString(52, HEIGHT - 31, "观动知姿")
                self.drawRightString(WIDTH - 52, HEIGHT - 31, "作品技术文档")
                self.setStrokeColor(LINE)
                self.line(52, HEIGHT - 40, WIDTH - 52, HEIGHT - 40)
            Canvas.showPage(self)
        Canvas.save(self)


def main():
    source = REPO / "docs/作品技术文档.md"
    pages = source.read_text(encoding="utf-8").split("---page---")
    story = []
    first = pages[0].strip().splitlines()
    story.append(Spacer(1, 70))
    for line in first:
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            story.append(para(line[2:], "cover"))
        elif line.startswith("## "):
            story.append(para(line[3:], "subtitle"))
            story.append(Spacer(1, 22))
        else:
            story.append(para(line, "meta"))
    story.append(Spacer(1, 44))
    story.append(cover_graphic())
    for page in pages[1:]:
        story.append(PageBreak())
        lines = page.strip().splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1
                continue
            if line.startswith("|"):
                table = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    table.append(lines[index])
                    index += 1
                story.append(render_table(table))
                story.append(Spacer(1, 12))
                continue
            if line.startswith("# "):
                story.append(para(line[2:], "h1"))
            elif line.startswith("## "):
                story.append(para(line[3:], "h2"))
            elif line.startswith("@diagram "):
                story.append(diagram(line.split(" ", 1)[1]))
            elif line.startswith("@image "):
                filename, caption = line[7:].split(" ", 1)
                image = Image(str(REPO / "docs/images" / filename))
                scale = min(CONTENT / image.imageWidth, 265 / image.imageHeight)
                image.drawWidth = image.imageWidth * scale
                image.drawHeight = image.imageHeight * scale
                story.append(KeepTogether([image, para(caption, "caption")]))
            else:
                story.append(para(line))
            index += 1
    output = OUT / "docs/观动知姿作品技术文档.pdf"
    doc = BaseDocTemplate(
        str(output), pagesize=A4, leftMargin=52, rightMargin=52,
        topMargin=62, bottomMargin=51, title="观动知姿作品技术文档",
        author="观动知姿项目", subject="AI WEB网页开发挑战赛",
    )
    frame = Frame(52, 51, CONTENT, HEIGHT - 113, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="normal", frames=[frame])])
    doc.build(story, canvasmaker=NumberedCanvas)
    print(str(output))


if __name__ == "__main__":
    main()
