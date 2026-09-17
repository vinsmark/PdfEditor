"""Privacy-minded, in-memory PDF editor built with Streamlit and PyMuPDF."""

from __future__ import annotations

import hashlib
import base64
import json
import math
import re
from pathlib import Path

import pymupdf as fitz
import streamlit as st

from visual_editor import visual_editor


MAX_UPLOAD_MB = 50


def pdf_color(value: int) -> tuple[float, float, float]:
    """Convert PyMuPDF's 0xRRGGBB span color to an insertion color."""
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


def compatible_font(font_name: str, flags: int) -> str:
    """Map a PDF font to the closest writable Base-14 fallback font."""
    name = font_name.lower()
    bold = bool(flags & 16) or "bold" in name
    italic = bool(flags & 2) or any(word in name for word in ("italic", "oblique"))
    mono = bool(flags & 8) or any(word in name for word in ("courier", "mono", "consolas"))
    serif = bool(flags & 4) or any(word in name for word in ("times", "serif", "georgia", "cambria"))
    if mono:
        return "cobi" if bold and italic else "cobo" if bold else "coit" if italic else "cour"
    if serif:
        return "tibi" if bold and italic else "tibo" if bold else "tiit" if italic else "tiro"
    return "hebi" if bold and italic else "hebo" if bold else "heit" if italic else "helv"


def normalized_font_name(font_name: str) -> str:
    """Normalize PDF subset names such as ABCDEF+Garamond-Bold."""
    name = re.sub(r"^[A-Z]{6}\+", "", font_name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def reusable_font(document: fitz.Document, pdf_page: fitz.Page, style: dict) -> fitz.Font:
    """Build a writable font from the span's embedded font, with a safe fallback."""
    wanted = normalized_font_name(str(style.get("font", "")))
    flags = int(style.get("flags", 0))
    wanted_bold = bool(flags & 16) or "bold" in wanted
    wanted_italic = bool(flags & 2) or "italic" in wanted or "oblique" in wanted
    candidates: list[tuple[int, int]] = []

    for font in pdf_page.get_fonts(full=True):
        xref, _extension, _font_type, base_name = font[:4]
        normalized = normalized_font_name(base_name)
        if normalized == wanted or normalized.startswith(wanted) or wanted.startswith(normalized):
            candidate_bold = "bold" in normalized
            candidate_italic = "italic" in normalized or "oblique" in normalized
            score = int(candidate_bold == wanted_bold) + int(candidate_italic == wanted_italic)
            candidates.append((score, xref))

    for _style_matches, xref in sorted(candidates, reverse=True):
        try:
            _name, _extension, _font_type, font_bytes = document.extract_font(xref)
            if font_bytes:
                return fitz.Font(fontbuffer=font_bytes)
        except Exception:
            continue

    return fitz.Font(fontname=compatible_font(str(style.get("font", "")), flags))


def style_for_match(pdf_page: fitz.Page, rectangle: fitz.Rect) -> dict:
    """Capture the appearance of the text span overlapping a search result."""
    best_style: dict | None = None
    best_area = -1.0
    for block in pdf_page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                overlap = fitz.Rect(span["bbox"]) & rectangle
                area = max(0.0, overlap.width) * max(0.0, overlap.height)
                if area > best_area:
                    best_area = area
                    best_style = span

    return best_style or {
        "size": 12.0,
        "color": 0,
        "alpha": 255,
        "font": "Helvetica",
        "flags": 0,
        "origin": (rectangle.x0, rectangle.y1),
    }


def write_styled_text(
    pdf_page: fitz.Page,
    point: tuple[float, float],
    text: str,
    font: fitz.Font,
    size: float,
    color: tuple[float, float, float],
    opacity: float = 1.0,
) -> None:
    """Write text using a Font object so embedded PDF fonts can be reused."""
    writer = fitz.TextWriter(pdf_page.rect)
    writer.append(point, text, font=font, fontsize=size)
    writer.write_text(pdf_page, color=color, opacity=opacity, overlay=True)


def edit_pdf(
    source_bytes: bytes,
    find_text: str,
    replacement: str,
    overlay: str,
    style_reference: str,
    page_number: int,
    x: float,
    y: float,
    font_size: float,
) -> tuple[bytes, int]:
    """Apply requested changes and return new PDF bytes plus replacement count."""
    if not find_text and not overlay:
        raise ValueError("Enter text to replace or add.")
    if x < 0 or y < 0 or not 4 <= font_size <= 96:
        raise ValueError("X/Y must be positive and font size must be between 4 and 96.")

    document = fitz.open(stream=source_bytes, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("Password-protected PDFs are not supported.")

        replacements = 0
        if find_text:
            for pdf_page in document:
                styled_matches: list[tuple[fitz.Rect, dict]] = []
                for rectangle in pdf_page.search_for(find_text):
                    style = style_for_match(pdf_page, rectangle)
                    style["write_font"] = reusable_font(document, pdf_page, style)
                    styled_matches.append((rectangle, style))

                for rectangle, _style in styled_matches:
                    pdf_page.add_redact_annot(rectangle, fill=None)
                if styled_matches:
                    pdf_page.apply_redactions()
                    for rectangle, style in styled_matches:
                        origin = style.get("origin", (rectangle.x0, rectangle.y1))
                        write_styled_text(
                            pdf_page,
                            (rectangle.x0, float(origin[1])),
                            replacement,
                            style["write_font"],
                            float(style.get("size", 12)),
                            pdf_color(int(style.get("color", 0))),
                            float(style.get("alpha", 255)) / 255,
                        )
                    replacements += len(styled_matches)

            if replacements == 0:
                raise ValueError(f'Exact text "{find_text}" was not found.')

        if overlay:
            if not 1 <= page_number <= document.page_count:
                raise ValueError(f"Page must be between 1 and {document.page_count}.")

            overlay_page = document[page_number - 1]
            if style_reference:
                style_matches = overlay_page.search_for(style_reference)
                if not style_matches:
                    raise ValueError(
                        f'Style reference "{style_reference}" was not found on page {page_number}.'
                    )
                overlay_style = style_for_match(overlay_page, style_matches[0])
                write_styled_text(
                    overlay_page,
                    (x, y),
                    overlay,
                    reusable_font(document, overlay_page, overlay_style),
                    float(overlay_style.get("size", font_size)),
                    pdf_color(int(overlay_style.get("color", 0))),
                    float(overlay_style.get("alpha", 255)) / 255,
                )
            else:
                overlay_page.insert_text(
                    (x, y), overlay, fontsize=font_size, fontname="helv", color=(0, 0, 0), overlay=True
                )

        return document.tobytes(garbage=4, deflate=True), replacements
    finally:
        document.close()


def page_text_boxes(pdf_page: fitz.Page) -> list[dict]:
    """Return stable, horizontal text-span records for visual editing."""
    boxes = []
    index = 0
    for block in pdf_page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            angle = math.degrees(math.atan2(-direction[1], direction[0]))
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                rectangle = fitz.Rect(span["bbox"])
                boxes.append({
                    "id": f"span-{index}",
                    "text": text,
                    "rect": rectangle,
                    "style": span,
                    "angle": angle,
                })
                index += 1
    return boxes


def visual_page_data(source_bytes: bytes, page_number: int) -> dict:
    """Create an exact rendered page preview with transparent editable hitboxes."""
    document = fitz.open(stream=source_bytes, filetype="pdf")
    try:
        if not 1 <= page_number <= document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}.")
        pdf_page = document[page_number - 1]
        records = page_text_boxes(pdf_page)
        pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)

        boxes = []
        for record in records:
            rectangle = record["rect"]
            style = record["style"]
            color = int(style.get("color", 0))
            font_name = str(style.get("font", "")).lower()
            family = "monospace" if any(x in font_name for x in ("courier", "mono")) else (
                "serif" if int(style.get("flags", 0)) & 4 else "sans-serif"
            )
            boxes.append({
                "id": record["id"],
                "text": record["text"],
                "x": rectangle.x0,
                "y": rectangle.y0,
                "width": rectangle.width,
                "height": rectangle.height,
                "size": float(style.get("size", 12)),
                "color": f"#{color:06x}",
                "family": family,
                "bold": bool(int(style.get("flags", 0)) & 16),
                "italic": bool(int(style.get("flags", 0)) & 2),
                "angle": record["angle"],
            })
        return {
            "image": base64.b64encode(pixmap.tobytes("png")).decode("ascii"),
            "width": pdf_page.rect.width,
            "height": pdf_page.rect.height,
            "boxes": boxes,
            "page_count": document.page_count,
        }
    finally:
        document.close()


def apply_visual_edits(source_bytes: bytes, page_number: int, payload: dict) -> tuple[bytes, int]:
    """Apply changed spans and manually placed text with inherited PDF styles."""
    document = fitz.open(stream=source_bytes, filetype="pdf")
    try:
        pdf_page = document[page_number - 1]
        records = {record["id"]: record for record in page_text_boxes(pdf_page)}
        pending = []
        for edit in payload.get("changes", []):
            record = records.get(str(edit.get("id", "")))
            if record is None:
                continue
            new_text = str(edit.get("text", ""))
            if new_text == record["text"]:
                continue
            style = record["style"]
            pending.append((record, new_text, reusable_font(document, pdf_page, style)))
            pdf_page.add_redact_annot(record["rect"], fill=None)

        additions = []
        for addition in payload.get("additions", []):
            reference = records.get(str(addition.get("reference", "")))
            text = str(addition.get("text", "")).strip()
            if reference is None or not text:
                continue
            style = dict(reference["style"])
            style["size"] = max(4.0, min(96.0, float(addition.get("size", style.get("size", 12)))))
            color_value = str(addition.get("color", f'#{int(style.get("color", 0)):06x}'))
            if re.fullmatch(r"#[0-9a-fA-F]{6}", color_value):
                style["color"] = int(color_value[1:], 16)
            flags = int(style.get("flags", 0))
            flags = flags | 16 if addition.get("bold") else flags & ~16
            flags = flags | 2 if addition.get("italic") else flags & ~2
            style["flags"] = flags
            baseline_offset = float(style.get("origin", (0, reference["rect"].y1))[1]) - reference["rect"].y0
            additions.append((
                (float(addition["x"]), float(addition["y"]) + baseline_offset),
                text,
                style,
                reusable_font(document, pdf_page, style),
            ))

        if not pending and not additions:
            raise ValueError("No text was changed or added.")
        if pending:
            pdf_page.apply_redactions(images=0, graphics=0)
        for record, new_text, font in pending:
            style = record["style"]
            origin = style.get("origin", (record["rect"].x0, record["rect"].y1))
            write_styled_text(
                pdf_page,
                (float(origin[0]), float(origin[1])),
                new_text,
                font,
                float(style.get("size", 12)),
                pdf_color(int(style.get("color", 0))),
                float(style.get("alpha", 255)) / 255,
            )
        for point, text, style, font in additions:
            write_styled_text(
                pdf_page,
                point,
                text,
                font,
                float(style.get("size", 12)),
                pdf_color(int(style.get("color", 0))),
                float(style.get("alpha", 255)) / 255,
            )
        return document.tobytes(garbage=4, deflate=True), len(pending) + len(additions)
    finally:
        document.close()


def clear_document() -> None:
    """Remove the working PDF from this session and reset the uploader."""
    for key in ("pdf_bytes", "pdf_name", "upload_digest", "revision", "visual_editing"):
        st.session_state.pop(key, None)
    st.session_state.uploader_version = st.session_state.get("uploader_version", 0) + 1


st.set_page_config(page_title="Private PDF Editor", page_icon="📄", layout="wide")

st.markdown(
    """
    <style>

    :root {
        --black: #111111;
        --text: #16181c;
        --muted: #52565c;
        --border: #d6d6d6;
        --background: #f7f7f7;
        --white: #ffffff;
    }

    * {
        box-sizing: border-box;
    }

    html, body, [data-testid="stAppViewContainer"] {
        background: var(--background);
    }

    body {
        color: var(--text);
    }

    #MainMenu, [data-testid="stToolbar"], [data-testid="stDecoration"],
    [data-testid="stStatusWidget"], .stDeployButton, footer {
        display: none !important;
    }

    .block-container {
        max-width: 1450px !important;
        padding: 32px 42px 50px !important;
    }

    /* Header */

    .app-title {
        color: #111111 !important;
        font-size: 22px !important;
        font-weight: 700 !important;
        letter-spacing: -0.35px;
        margin-bottom: 8px;
    }

    .privacy-note {
        padding: 10px 14px;
        border-radius: 6px;
        background: #f2f2f2;
        border: 1px solid var(--border);
        color: var(--muted);
        font-size: 12.5px;
        font-weight: 500;
        margin-bottom: 22px;
    }

    /* Preview / control cards */

    .st-key-preview_card {
        background: #eeeeee !important;
        border: 1px solid var(--border) !important;
        border-radius: 7px !important;
        min-height: 880px;
        padding: 20px !important;
    }

    .st-key-control_card {
        background: var(--white) !important;
        border: 1px solid var(--border) !important;
        border-radius: 7px !important;
        padding: 28px !important;
        min-height: 880px;
    }

    .control-title {
        font-size: 16px;
        font-weight: 700;
        color: #111111;
        margin-bottom: 16px;
    }

    .file-name {
        font-size: 14px;
        font-weight: 500;
        color: #2c2f34;
        margin-top: 4px;
        margin-bottom: 4px;
        word-break: break-word;
    }

    .divider {
        width: 100%;
        height: 1px;
        background: #e0e0e0;
        margin: 18px 0;
    }

    .success-message {
        margin-top: 12px;
        padding: 10px 12px;
        border: 1px solid #c9c9c9;
        border-radius: 6px;
        background: #f2f2f2;
        font-size: 13px;
        font-weight: 500;
        color: #111111;
    }

    /* Buttons: black and white only, no red or teal accents */

    .stButton > button,
    .stDownloadButton > button {
        min-height: 50px !important;
        border-radius: 6px !important;
        font-size: 14.5px !important;
        font-weight: 600 !important;
        box-shadow: none !important;
        transition: 0.15s ease;
        width: 100%;
    }

    .stButton > button[kind="primary"] {
        background: #111111 !important;
        color: #ffffff !important;
        border: 1px solid #111111 !important;
    }

    .stButton > button[kind="primary"]:hover {
        background: #303030 !important;
        border-color: #303030 !important;
        color: #ffffff !important;
    }

    .stButton > button[kind="secondary"] {
        background: #ffffff !important;
        color: #111111 !important;
        border: 1px solid #111111 !important;
    }

    .stButton > button[kind="secondary"]:hover {
        background: #f2f2f2 !important;
        color: #111111 !important;
    }

    .stDownloadButton > button {
        background: #ffffff !important;
        color: #111111 !important;
        border: 1.5px solid #111111 !important;
    }

    .stDownloadButton > button:hover {
        background: #111111 !important;
        color: #ffffff !important;
    }

    /* Number input (page selector) */

    [data-testid="stNumberInput"] input {
        border-radius: 6px !important;
        border: 1px solid #b8b8b8 !important;
        background: white !important;
        color: #111111 !important;
        font-weight: 600 !important;
    }

    /* File uploader */

    [data-testid="stFileUploaderDropzone"] {
        border: 1px solid #cfcfcf !important;
        border-radius: 6px !important;
        background: #fafafa !important;
    }

    [data-testid="stFileUploaderDropzone"]:hover {
        border-color: #9a9a9a !important;
        background: #f5f5f5 !important;
    }

    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stFileUploaderDropzone"]:hover [data-testid="stFileUploaderDropzoneInstructions"] {
        color: #4a4a4a !important;
        font-weight: 500 !important;
    }

    [data-testid="stFileUploaderDropzoneInstructions"] * {
        color: inherit !important;
    }

    [data-testid="stFileUploaderDropzone"] svg {
        color: #4a4a4a !important;
        fill: #4a4a4a !important;
    }

    [data-testid="stFileUploaderDropzone"] button {
        color: #111111 !important;
        background: #ffffff !important;
        border: 1px solid #111111 !important;
    }

    [data-testid="stFileUploaderDropzone"] button:hover {
        color: #111111 !important;
        background: #f2f2f2 !important;
        border-color: #111111 !important;
    }

    *:focus-visible {
        outline-color: #111111 !important;
    }

    /* Mobile responsiveness */

    @media (max-width: 950px) {

        .block-container {
            padding: 22px 18px 35px !important;
        }

        .st-key-preview_card {
            min-height: 560px;
        }

        .st-key-control_card {
            margin-top: 18px;
            min-height: 0;
        }
    }

    @media (max-width: 600px) {

        .block-container {
            padding: 16px 12px 25px !important;
        }

        .app-title {
            font-size: 19px !important;
        }

        .st-key-preview_card {
            min-height: 420px;
            border-radius: 6px;
            padding: 10px !important;
        }

        .st-key-control_card {
            padding: 16px !important;
        }
    }

    </style>
    """,
    unsafe_allow_html=True,
)

if "uploader_version" not in st.session_state:
    st.session_state.uploader_version = 0

st.markdown('<div class="app-title">Private PDF Editor</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="privacy-note">PDFs are processed in this session\'s memory. '
    "This app does not write uploaded or edited PDFs to disk.</div>",
    unsafe_allow_html=True,
)

uploaded = st.file_uploader(
    "Open a PDF",
    type=["pdf"],
    accept_multiple_files=False,
    max_upload_size=MAX_UPLOAD_MB,
    key=f"pdf_uploader_{st.session_state.uploader_version}",
)

if uploaded is not None:
    uploaded_bytes = uploaded.getvalue()
    digest = hashlib.sha256(uploaded_bytes).hexdigest()
    if digest != st.session_state.get("upload_digest"):
        if not uploaded_bytes.startswith(b"%PDF-"):
            st.error("The selected file is not a valid PDF.")
        else:
            try:
                probe = fitz.open(stream=uploaded_bytes, filetype="pdf")
                page_count = probe.page_count
                probe.close()
                if page_count < 1:
                    raise ValueError("The PDF contains no pages.")
                st.session_state.pdf_bytes = uploaded_bytes
                st.session_state.pdf_name = Path(uploaded.name).name
                st.session_state.upload_digest = digest
                st.session_state.revision = 0
                st.session_state.visual_editing = False
            except Exception as error:
                st.error(f"Could not open PDF: {error}")

if "pdf_bytes" not in st.session_state:
    st.markdown(
        '<div class="privacy-note">Choose a PDF above to start editing. '
        "No file will be saved to the project folder.</div>",
        unsafe_allow_html=True,
    )
    st.stop()

edit_notice = st.session_state.pop("edit_notice", None)

current_name = st.session_state.pdf_name
document_probe = fitz.open(stream=st.session_state.pdf_bytes, filetype="pdf")
page_count = document_probe.page_count
document_probe.close()

# The page selector lives in the right-hand control card, but the editor in
# the left column needs its value first, so it is read from session state
# here and clamped to the current document.
st.session_state.page_to_edit_input = max(
    1, min(int(st.session_state.get("page_to_edit_input", 1)), page_count)
)
page_number = int(st.session_state.page_to_edit_input)
visual_editing = st.session_state.get("visual_editing", False)


# ============================================================
# TWO-COLUMN WORKSPACE
# LEFT  = PDF PREVIEW (component runs here)
# RIGHT = DOCUMENT CONTROLS
#
# Column position is fixed by the order st.columns() is called,
# regardless of which `with` block runs first below.
# ============================================================

left_column, right_column = st.columns([2.9, 1.2], gap="large")

with left_column:

    with st.container(border=True, key="preview_card"):

        if visual_editing:
            try:
                editor_data = visual_page_data(st.session_state.pdf_bytes, page_number)
                component_key = f"visual_editor_{st.session_state.revision}_{page_number}"
                result = visual_editor(
                    editor_data,
                    key=component_key,
                )
                edits_payload = getattr(result, "edits", "")
                if not edits_payload:
                    component_state = st.session_state.get(component_key, {})
                    edits_payload = component_state.get("edits", "") if component_state else ""
                if edits_payload:
                    visual_edits = json.loads(edits_payload)
                    updated_bytes, changed_count = apply_visual_edits(
                        st.session_state.pdf_bytes, page_number, visual_edits
                    )
                    st.session_state.pdf_bytes = updated_bytes
                    st.session_state.revision += 1
                    st.session_state.edit_notice = (
                        f"Saved {changed_count} text-box change(s) in memory. "
                        "The download is now updated."
                    )
                    st.rerun()
            except Exception as error:
                st.error(f"Could not open the visual editor: {error}")
        else:
            st.pdf(st.session_state.pdf_bytes, height=1000)

with right_column:

    with st.container(border=True, key="control_card"):

        st.markdown('<div class="control-title">Document</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="file-name">{current_name}</div>', unsafe_allow_html=True)

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        edit_label = "Close editor" if visual_editing else "Edit PDF"
        if st.button(edit_label, type="primary", use_container_width=True, key="toggle_edit"):
            st.session_state.visual_editing = not visual_editing
            st.rerun()

        if visual_editing:
            st.number_input(
                "Page to edit",
                min_value=1,
                max_value=page_count,
                step=1,
                key="page_to_edit_input",
            )

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        st.download_button(
            "Download PDF",
            data=st.session_state.pdf_bytes,
            file_name=f"edited_{Path(current_name).stem}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

        st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        if st.button("Clear PDF", use_container_width=True, key="clear_pdf"):
            clear_document()
            st.rerun()

        if edit_notice:
            st.markdown(
                f'<div class="success-message">{edit_notice}</div>',
                unsafe_allow_html=True,
            )