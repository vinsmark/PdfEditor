"""Privacy-minded, in-memory PDF editor built with Streamlit and PyMuPDF."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf as fitz
import streamlit as st


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
    candidates: list[tuple[bool, int]] = []

    for font in pdf_page.get_fonts(full=True):
        xref, _extension, _font_type, base_name = font[:4]
        normalized = normalized_font_name(base_name)
        if normalized == wanted or normalized.startswith(wanted) or wanted.startswith(normalized):
            candidates.append((("bold" in normalized) == wanted_bold, xref))

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


def clear_document() -> None:
    """Remove the working PDF from this session and reset the uploader."""
    for key in ("pdf_bytes", "pdf_name", "upload_digest", "revision"):
        st.session_state.pop(key, None)
    st.session_state.uploader_version = st.session_state.get("uploader_version", 0) + 1


st.set_page_config(page_title="Private PDF Editor", page_icon="📄", layout="wide")
st.markdown(
    """
    <style>
      .block-container { max-width: 100%; padding: 1rem 1.25rem 2rem; }
      .privacy-note { padding: .7rem 1rem; border-radius: .5rem; background: rgba(30,120,80,.12);
                      border: 1px solid rgba(50,160,110,.35); }
      #MainMenu, [data-testid="stToolbar"], [data-testid="stDecoration"],
      [data-testid="stStatusWidget"] { display: none !important; }
      [data-testid="stForm"] { padding: .75rem; }
      [data-testid="stForm"] h3 { margin-top: .25rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "uploader_version" not in st.session_state:
    st.session_state.uploader_version = 0

st.title("Private PDF Editor")
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
            except Exception as error:
                st.error(f"Could not open PDF: {error}")

if "pdf_bytes" not in st.session_state:
    st.info("Choose a PDF above to start editing. No file will be saved to the project folder.")
    st.stop()

viewer_column, editor_column = st.columns([4, 1], gap="small")

with editor_column:
    current_name = st.session_state.pdf_name

    with st.form("pdf_editor"):
        st.subheader("Replace text")
        find_text = st.text_area("Find exact text", height=60)
        replacement = st.text_area("Replace with", height=60)
        st.caption("Every match inherits its original embedded font, size, color, opacity, and baseline.")

        st.subheader("Add text")
        overlay = st.text_area("Text to add", height=60)
        style_reference = st.text_input(
            "Match style from existing text",
            placeholder="Example: Admin Staff",
            help="Copies the appearance of matching text already visible on the selected page.",
        )
        position_a, position_b, position_c = st.columns(3)
        page_number = position_a.number_input("Page", min_value=1, value=1, step=1)
        x = position_b.number_input("X", min_value=0.0, value=72.0, step=1.0)
        y = position_c.number_input("Y", min_value=0.0, value=72.0, step=1.0)
        font_size = st.number_input(
            "Font size when not matching", min_value=4.0, max_value=96.0, value=12.0, step=1.0
        )
        st.caption("Coordinates use PDF points from the page's top-left. 72 points equals 1 inch.")
        apply_edit = st.form_submit_button("Apply edit", type="primary", use_container_width=True)

    if apply_edit:
        try:
            edited_bytes, replacement_count = edit_pdf(
                st.session_state.pdf_bytes,
                find_text,
                replacement,
                overlay,
                style_reference,
                int(page_number),
                float(x),
                float(y),
                float(font_size),
            )
            st.session_state.pdf_bytes = edited_bytes
            st.session_state.revision += 1
            actions = []
            if find_text:
                actions.append(f"replaced {replacement_count} match(es)")
            if overlay:
                actions.append(f"added text on page {int(page_number)}")
            st.success("Updated in memory: " + " and ".join(actions) + ".")
        except Exception as error:
            st.error(f"Could not edit PDF: {error}")

    download_name = f"edited_{Path(current_name).stem}.pdf"
    st.download_button(
        "Download current PDF",
        data=st.session_state.pdf_bytes,
        file_name=download_name,
        mime="application/pdf",
        use_container_width=True,
    )
    if st.button("Clear PDF from memory", use_container_width=True):
        clear_document()
        st.rerun()

with viewer_column:
    st.subheader(st.session_state.pdf_name)
    st.pdf(st.session_state.pdf_bytes, height=1000)
