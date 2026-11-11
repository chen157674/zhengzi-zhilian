"""只调整现有参赛文档的插图与图注位置，保留正文、字体、图片和PDF分页。"""
from pathlib import Path
from io import BytesIO
import argparse
import hashlib
import json
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from pypdf import PdfReader, PdfWriter
from pypdf._font import Font
from pypdf._text_extraction import mult
from pypdf.generic import ContentStream, FloatObject, TextStringObject

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "OPC专项赛"
CAPTION = re.compile(r"^图\s*\d+")


def normalized(value):
    return re.sub(r"\s+", "", value)


def decode(value, font):
    raw = value.original_bytes if isinstance(value, TextStringObject) else bytes(value)
    if isinstance(font.encoding, str):
        raw_text = raw.decode(font.encoding, errors="surrogatepass")
    else:
        raw_text = "".join(font.encoding.get(v, chr(v)) for v in raw)
    return "".join(font.character_map.get(char, char) for char in raw_text)


def caption_groups(page):
    fonts = {name: Font.from_font_resource(obj.get_object())
             for name, obj in page.get("/Resources", {}).get("/Font", {}).items()}
    operations = page.get_contents().operations
    matrix = [1., 0., 0., 1., 0., 0.]
    stack = []
    active = None
    size = 0.
    index = 0
    result = []
    while index < len(operations):
        operands, operator = operations[index]
        if operator == b"q":
            stack.append((matrix[:], active, size))
        elif operator == b"Q":
            matrix, active, size = stack.pop()
        elif operator == b"cm":
            matrix = mult([float(v) for v in operands], matrix)
        elif operator == b"Tf":
            active, size = fonts[operands[0]], float(operands[1])
        elif operator == b"BT":
            start = index
            parts = []
            width = 0.
            first_matrix = None
            text_matrix = [1., 0., 0., 1., 0., 0.]
            line = 0
            occupied_lines = set()
            while index < len(operations):
                values, op = operations[index]
                if op == b"Tf":
                    active, size = fonts[values[0]], float(values[1])
                elif op == b"Tm":
                    text_matrix = [float(v) for v in values]
                elif op == b"T*":
                    line += 1
                elif op in {b"Tj", b"TJ"}:
                    if first_matrix is None:
                        first_matrix = text_matrix[:]
                    items = values[0] if op == b"TJ" else [values[0]]
                    for item in items:
                        if isinstance(item, (int, float)):
                            width -= float(item) * size / 1000
                            continue
                        text = decode(item, active)
                        parts.append(text)
                        width += active.text_width(text) * size / 1000
                        if text:
                            occupied_lines.add(line)
                if op == b"ET":
                    break
                index += 1
            text = "".join(parts).strip()
            if CAPTION.match(text):
                if len(occupied_lines) != 1 or abs(matrix[1]) > 1e-6 or abs(matrix[2]) > 1e-6:
                    raise ValueError("遇到需单独处理的多行或旋转图注：" + text)
                position = mult(first_matrix, matrix)
                center = (float(page.mediabox.left) + float(page.mediabox.right)) / 2
                actual_width = width * position[0]
                left = position[4]
                shift = center - actual_width / 2 - left
                result.append({"start": start, "end": index, "text": text,
                               "left": left, "y": position[5], "width": actual_width,
                               "center": left + actual_width / 2, "page_center": center,
                               "local_shift": shift / matrix[0]})
        index += 1
    return result


def process_word(path, apply):
    document = Document(path)
    pictures = captions = 0
    before = [p.text for p in document.paragraphs]
    for node in document.element.body.xpath(".//w:p"):
        paragraph = Paragraph(node, document)
        is_picture = bool(node.xpath(".//w:drawing | .//w:pict"))
        is_caption = bool(CAPTION.match(paragraph.text.strip()))
        if not (is_picture or is_caption):
            continue
        pictures += int(is_picture)
        captions += int(is_caption)
        if apply:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.first_line_indent = Pt(0)
            paragraph.paragraph_format.left_indent = Pt(0)
            paragraph.paragraph_format.right_indent = Pt(0)
            if is_picture:
                paragraph.paragraph_format.keep_with_next = True
    if apply and (pictures or captions):
        if before != [p.text for p in document.paragraphs]:
            raise ValueError("Word正文发生非预期变化")
        document.save(path)
    return {"file": path.name, "pictures": pictures, "captions": captions}


def process_pdf(path, apply):
    raw = path.read_bytes()
    reader = PdfReader(BytesIO(raw))
    before = [normalized(page.extract_text()) for page in reader.pages]
    changes = []
    image_count = 0
    for number, page in enumerate(reader.pages, 1):
        for caption in caption_groups(page):
            if abs(caption["center"] - caption["page_center"]) > .2:
                changes.append((number - 1, caption))

        def image_position(operator, operands, matrix, text_matrix):
            nonlocal image_count
            if operator != b"Do":
                return
            image = page.get("/Resources", {}).get("/XObject", {}).get(operands[0])
            if not image or image.get_object().get("/Subtype") != "/Image":
                return
            image_count += 1
            center = matrix[4] + matrix[0] / 2
            page_center = (float(page.mediabox.left) + float(page.mediabox.right)) / 2
            if abs(center - page_center) > .5:
                raise ValueError(f"{path.name}第{number}页有未居中的图片，需单独调整")

        page.extract_text(visitor_operand_before=image_position)
    if apply and changes:
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        for number, page in enumerate(writer.pages):
            replacements = {item["start"]: item for page_no, item in changes if page_no == number}
            if not replacements:
                continue
            operations = page.get_contents().operations
            result = []
            index = 0
            while index < len(operations):
                if index in replacements:
                    item = replacements[index]
                    result.extend([([], b"q"),
                                   ([FloatObject(v) for v in [1, 0, 0, 1, item["local_shift"], 0]], b"cm")])
                    result.extend(operations[index:item["end"] + 1])
                    result.append(([], b"Q"))
                    index = item["end"] + 1
                else:
                    result.append(operations[index])
                    index += 1
            stream = ContentStream(None, writer)
            stream.operations = result
            page.replace_contents(stream)
        output = BytesIO()
        writer.write(output)
        verified = PdfReader(BytesIO(output.getvalue()))
        if len(verified.pages) != len(reader.pages):
            raise ValueError("PDF页数发生变化")
        if [normalized(page.extract_text()) for page in verified.pages] != before:
            raise ValueError("PDF正文发生非预期变化")
        for page in verified.pages:
            for caption in caption_groups(page):
                if abs(caption["center"] - caption["page_center"]) > .2:
                    raise ValueError("PDF图注居中验证失败")
        path.write_bytes(output.getvalue())
    current = PdfReader(BytesIO(path.read_bytes()))
    return {"file": path.name, "pages": len(current.pages), "images": image_count,
            "centered_captions": [{"page": number, "text": c["text"],
                                  "center_error_pt": round(abs(c["center"] - c["page_center"]), 4)}
                                 for number, page in enumerate(current.pages, 1)
                                 for c in caption_groups(page)],
            "changed_captions": len(changes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    words = [process_word(path, args.apply) for path in sorted(OUT.glob("知练同行_*.docx"))]
    pdfs = [process_pdf(path, args.apply) for path in sorted(OUT.glob("知练同行_*.pdf"))]
    if args.apply:
        target = OUT / "补充成果附件/E08_文档核验.json"
        data = json.loads(target.read_text(encoding="utf8"))
        for row in data["文档"]:
            row["SHA256"] = hashlib.sha256((OUT / row["文件"]).read_bytes()).hexdigest()
        data["图文居中检查"] = {"Word": words, "PDF": pdfs,
                              "检查": "Word插图及图注显式居中、PDF图注中心误差不超过0.2pt、正文与PDF页数不变"}
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"Word": words, "PDF": pdfs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
