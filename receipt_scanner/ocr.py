from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass
from typing import Literal, Optional

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from PIL import Image, ImageOps


@dataclass
class OcrResult:
    text: str
    engine: Literal["tesseract", "pdf_text"]
    notes: Optional[str] = None


def _ensure_tesseract_cmd() -> None:
    # 某些情况下（例如从 IDE/GUI 启动），PATH 不包含 brew 的 bin，
    # 导致 pytesseract 找不到 tesseract。这里做一次常见路径兜底。
    if shutil.which("tesseract"):
        return

    candidates = [
        "/opt/homebrew/bin/tesseract",  # Apple Silicon Homebrew
        "/usr/local/bin/tesseract",  # Intel Homebrew
        "/opt/local/bin/tesseract",  # MacPorts
    ]
    for p in candidates:
        if os.path.exists(p) and os.access(p, os.X_OK):
            pytesseract.pytesseract.tesseract_cmd = p
            return


def _prep_image(img: Image.Image) -> Image.Image:
    # 轻量预处理：灰度 + 自适应对比度 + 轻微放大
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    if max(img.size) < 1600:
        scale = 1600 / max(img.size)
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)))
    return img


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    lang: str = "chi_sim+eng",
) -> OcrResult:
    _ensure_tesseract_cmd()
    img = Image.open(io.BytesIO(image_bytes))
    img = _prep_image(img)
    text = pytesseract.image_to_string(img, lang=lang)
    return OcrResult(text=text, engine="tesseract")


def ocr_pdf_bytes(
    pdf_bytes: bytes,
    *,
    lang: str = "chi_sim+eng",
    max_pages: int = 6,
) -> OcrResult:
    # 先尝试直接提取 PDF 文字（速度快、质量高）
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texts = []
            for i, page in enumerate(pdf.pages[:max_pages]):
                t = (page.extract_text() or "").strip()
                if t:
                    texts.append(t)
        combined = "\n\n".join(texts).strip()
        if combined:
            return OcrResult(text=combined, engine="pdf_text", notes="PDF 含可复制文本，未走 OCR。")
    except Exception:
        pass

    # 否则将每页渲染为图片再 OCR（适用于扫描件）
    _ensure_tesseract_cmd()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = []
    pages = min(len(doc), max_pages)
    for idx in range(pages):
        page = doc.load_page(idx)
        # 2x 缩放渲染，平衡速度与识别率
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = _prep_image(img)
        texts.append(pytesseract.image_to_string(img, lang=lang))
    doc.close()
    return OcrResult(text="\n\n".join(texts).strip(), engine="tesseract", notes="PDF 扫描件，逐页渲染后 OCR。")

