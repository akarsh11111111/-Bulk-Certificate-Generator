"""
certificate_engine.py
─────────────────────
Core certificate generation engine.

Features:
  • Pillow-based PNG rendering (full colour, anti-aliased fonts).
  • fpdf2-based PDF rendering with embedded image support.
  • Multiprocessing pool for parallel batch generation (CPU-bound work).
  • In-memory buffers: no intermediate files written to disk.
  • Progress reporting via a shared multiprocessing.Value counter.
    *simple to use and implement 
Public API
──────────
    generate_single(record, template_img, layout, fmt) → bytes
    generate_batch(df, template_img, layout, fmt, progress_cb) → list[GeneratedCert]

GeneratedCert namedtuple:
    name      – str    participant name (used for filename)
    fmt       – str    "png" | "pdf"
    data      – bytes  file bytes ready to write / zip
"""

import io
import os
import copy
import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class GeneratedCert:
    """Container for one generated certificate."""
    name: str       # Participant name — used as filename base
    fmt:  str       # "png" | "pdf"
    data: bytes     # Raw file bytes


# ─── Font helper (duplicated from template_handler to avoid cross-import) ─────

# Font paths matching template_handler.py catalogue
_FONT_CATALOGUE: dict[str, tuple[str, str]] = {
    "Arial":           ("C:/Windows/Fonts/arial.ttf",    "C:/Windows/Fonts/arialbd.ttf"),
    "Georgia":         ("C:/Windows/Fonts/georgia.ttf",  "C:/Windows/Fonts/georgiab.ttf"),
    "Times New Roman": ("C:/Windows/Fonts/times.ttf",    "C:/Windows/Fonts/timesbd.ttf"),
    "Trebuchet MS":    ("C:/Windows/Fonts/trebuc.ttf",   "C:/Windows/Fonts/trebucbd.ttf"),
    "Verdana":         ("C:/Windows/Fonts/verdana.ttf",  "C:/Windows/Fonts/verdanab.ttf"),
    "Courier New":     ("C:/Windows/Fonts/cour.ttf",     "C:/Windows/Fonts/courbd.ttf"),
    "Impact":          ("C:/Windows/Fonts/impact.ttf",   "C:/Windows/Fonts/impact.ttf"),
    "Palatino":        ("C:/Windows/Fonts/pala.ttf",     "C:/Windows/Fonts/palab.ttf"),
    "Tahoma":          ("C:/Windows/Fonts/tahoma.ttf",   "C:/Windows/Fonts/tahomabd.ttf"),
}


def _get_font(size: int, bold: bool = False, family: str = "Arial") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return the best available PIL font for the given family, size, and weight."""
    reg, bd = _FONT_CATALOGUE.get(family, _FONT_CATALOGUE["Arial"])
    primary   = bd if bold else reg
    fallbacks = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in [primary] + fallbacks:
        try:
            return ImageFont.truetype(str(p), size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


# ─── PNG generation ───────────────────────────────────────────────────────────

def _render_png(
    record: dict,
    template_bytes: bytes,
    layout: list[dict],
) -> bytes:
    """
    Render one certificate as PNG bytes using Pillow.

    Args:
        record:         Participant data dict {column: value}.
        template_bytes: Raw bytes of the base template image.
        layout:         List of field-layout dicts.

    Returns:
        PNG bytes of the completed certificate.
    """
    img  = Image.open(io.BytesIO(template_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    for field in layout:
        # Substitute placeholders with participant values
        text = field["placeholder"]
        for key, val in record.items():
            text = text.replace(f"{{{key}}}", str(val))

        x = int(field["x_pct"] / 100 * W)
        y = int(field["y_pct"] / 100 * H)

        font  = _get_font(field["font_size"], field.get("bold", False), field.get("font_family", "Arial"))
        color = field.get("font_color", "#000000")
        align = field.get("align", "center")

        # Measure text width for alignment
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw   = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(text, font=font)

        if align == "center":
            x -= tw // 2
        elif align == "right":
            x -= tw

        # Drop shadow
        draw.text((x + 1, y + 1), text, font=font, fill="#00000044")
        draw.text((x, y),         text, font=font, fill=color)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ─── PDF generation ───────────────────────────────────────────────────────────

def _render_pdf(
    record: dict,
    template_bytes: bytes,
    layout: list[dict],
) -> bytes:
    """
    Render one certificate as a PDF using fpdf2 (image-backed page).

    The template image is embedded as the full-page background; text is
    overlaid using fpdf2's cell / text primitives.

    Args:
        record:         Participant data dict.
        template_bytes: Raw bytes of the base template image.
        layout:         List of field-layout dicts.

    Returns:
        PDF bytes.
    """
    from fpdf import FPDF

    # ── Build a high-quality PNG first then embed into PDF ──────────────────
    # Scale to A4 landscape at 150 DPI for a nice PDF
    TARGET_W_PX = 1587   # A4 landscape @ 150 dpi ≈ 1587 × 1122
    TARGET_H_PX = 1122

    base = Image.open(io.BytesIO(template_bytes)).convert("RGB")
    base = base.resize((TARGET_W_PX, TARGET_H_PX), Image.LANCZOS)

    # Render text onto the image (same as PNG path)
    draw = ImageDraw.Draw(base)
    W, H = base.size

    for field in layout:
        text = field["placeholder"]
        for key, val in record.items():
            text = text.replace(f"{{{key}}}", str(val))

        x = int(field["x_pct"] / 100 * W)
        y = int(field["y_pct"] / 100 * H)

        font  = _get_font(field["font_size"], field.get("bold", False), field.get("font_family", "Arial"))
        color = field.get("font_color", "#000000")
        align = field.get("align", "center")

        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw   = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(text, font=font)

        if align == "center":
            x -= tw // 2
        elif align == "right":
            x -= tw

        draw.text((x + 1, y + 1), text, font=font, fill="#00000044")
        draw.text((x, y),         text, font=font, fill=color)

    # ── Write rendered image into PDF ────────────────────────────────────────
    img_buf = io.BytesIO()
    base.save(img_buf, format="PNG")
    img_buf.seek(0)

    pdf = FPDF(orientation="L", unit="mm", format="A4")  # A4 landscape
    pdf.add_page()
    pdf.image(img_buf, x=0, y=0, w=297, h=210)           # A4: 297×210 mm

    return bytes(pdf.output())   # fpdf2 returns bytearray; Streamlit needs bytes


# ─── Worker function (must be top-level for multiprocessing pickling) ─────────

def _worker(args: tuple) -> GeneratedCert:
    """
    Unpickle arguments and generate one certificate.
    This function runs in a child process.

    Args:
        args: (record, template_bytes, layout, fmt)

    Returns:
        GeneratedCert dataclass.
    """
    record, template_bytes, layout, fmt = args

    if fmt == "pdf":
        data = _render_pdf(record, template_bytes, layout)
    else:
        data = _render_png(record, template_bytes, layout)

    # Use the first column's value as the filename base (no hardcoded 'Name')
    name = next(iter(record.values()), "certificate") if record else "certificate"
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in str(name)).strip() or "certificate"
    return GeneratedCert(name=safe_name, fmt=fmt, data=data)


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_single(
    record: dict,
    template_img: Image.Image,
    layout: list[dict],
    fmt: str = "png",
) -> bytes:
    """
    Generate one certificate synchronously and return raw bytes.

    Args:
        record:       Single participant dict.
        template_img: PIL Image of the base template.
        layout:       List of field-layout dicts.
        fmt:          "png" or "pdf".

    Returns:
        Bytes of the rendered certificate.
    """
    buf = io.BytesIO()
    template_img.save(buf, format="PNG")
    template_bytes = buf.getvalue()

    result = _worker((record, template_bytes, layout, fmt))
    return result.data


def generate_batch(
    df: pd.DataFrame,
    template_img: Image.Image,
    layout: list[dict],
    fmt: str = "png",
    progress_cb: Callable[[int, int], None] | None = None,
    max_workers: int | None = None,
) -> list[GeneratedCert]:
    """
    Generate certificates for all rows in *df* using a multiprocessing pool.

    Args:
        df:           Validated participant DataFrame.
        template_img: PIL Image of the base template.
        layout:       List of field-layout dicts.
        fmt:          "png" or "pdf".
        progress_cb:  Optional callable(completed, total) for UI progress updates.
        max_workers:  Number of worker processes.  Defaults to CPU count − 1
                      (minimum 1).

    Returns:
        List of GeneratedCert objects in participant order.
    """
    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 2) - 1)

    # Serialise template once (avoids pickling PIL Image — not picklable)
    buf = io.BytesIO()
    template_img.save(buf, format="PNG")
    template_bytes = buf.getvalue()

    records = df.to_dict(orient="records")
    total   = len(records)

    args_list = [
        (record, template_bytes, layout, fmt)
        for record in records
    ]

    results: list[GeneratedCert] = []

    # Use multiprocessing for batches > 5, sequential otherwise (avoids spawn
    # overhead for tiny datasets and plays nicely with Streamlit's main thread)
    if total > 5 and max_workers > 1:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=max_workers) as pool:
            for i, cert in enumerate(pool.imap(_worker, args_list), start=1):
                results.append(cert)
                if progress_cb:
                    progress_cb(i, total)
    else:
        for i, args in enumerate(args_list, start=1):
            results.append(_worker(args))
            if progress_cb:
                progress_cb(i, total)

    return results
