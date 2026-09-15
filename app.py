"""A tiny, dependency-free PDF viewer web application."""

from __future__ import annotations

import argparse
import html
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def unique_pdf_path(filename: str) -> Path:
    """Return a safe, unused path inside UPLOAD_DIR."""
    clean_name = Path(filename).name
    clean_name = SAFE_FILENAME.sub("_", clean_name).strip(" .") or "document.pdf"
    if not clean_name.lower().endswith(".pdf"):
        clean_name += ".pdf"

    candidate = UPLOAD_DIR / clean_name
    stem, suffix = candidate.stem, candidate.suffix
    number = 2
    while candidate.exists():
        candidate = UPLOAD_DIR / f"{stem} ({number}){suffix}"
        number += 1
    return candidate


def pdf_color(value: int) -> tuple[float, float, float]:
    """Convert PyMuPDF's 0xRRGGBB span color to an insert_text color."""
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


def compatible_font(font_name: str, flags: int) -> str:
    """Map a PDF font to the closest reliably writable Base-14 font."""
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
    """Normalize PDF subset font names such as ABCDEF+Garamond-Bold."""
    name = re.sub(r"^[A-Z]{6}\+", "", font_name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def reusable_font(document: object, pdf_page: object, style: dict[str, object]) -> object:
    """Build a writable font from the span's embedded font, with a safe fallback."""
    wanted = normalized_font_name(str(style.get("font", "")))
    flags = int(style.get("flags", 0))
    wanted_bold = bool(flags & 16) or "bold" in wanted
    candidates = []
    for font in pdf_page.get_fonts(full=True):
        xref, _extension, _font_type, base_name = font[:4]
        normalized = normalized_font_name(base_name)
        if normalized == wanted or normalized.startswith(wanted) or wanted.startswith(normalized):
            candidate_bold = "bold" in normalized
            candidates.append((candidate_bold == wanted_bold, xref))
    for _style_matches, xref in sorted(candidates, reverse=True):
        try:
            _name, _extension, _font_type, font_bytes = document.extract_font(xref)
            if font_bytes:
                return fitz.Font(fontbuffer=font_bytes)
        except Exception:
            continue
    return fitz.Font(fontname=compatible_font(str(style.get("font", "")), flags))


def write_styled_text(pdf_page: object, point: tuple[float, float], text: str,
                      font: object, size: float, color: tuple[float, float, float],
                      opacity: float = 1.0) -> None:
    """Write text using a Font object, allowing extracted embedded fonts."""
    writer = fitz.TextWriter(pdf_page.rect)
    writer.append(point, text, font=font, fontsize=size)
    writer.write_text(pdf_page, color=color, opacity=opacity, overlay=True)


def style_for_match(pdf_page: object, rectangle: object) -> dict[str, object]:
    """Find the text span that overlaps a search result and capture its appearance."""
    best_style: dict[str, object] | None = None
    best_area = -1.0
    for block in pdf_page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_rect = fitz.Rect(span["bbox"])
                overlap = span_rect & rectangle
                area = max(0.0, overlap.width) * max(0.0, overlap.height)
                if area > best_area:
                    best_area = area
                    best_style = span
    if not best_style:
        return {"size": 12.0, "color": 0, "font": "Helvetica", "flags": 0,
                "origin": (rectangle.x0, rectangle.y1)}
    return best_style


def page(message: str = "", selected: str = "") -> bytes:
    files = sorted(UPLOAD_DIR.glob("*.pdf"), key=lambda item: item.name.lower())
    options = "".join(
        f'<option value="{html.escape(item.name)}"'
        f'{" selected" if item.name == selected else ""}>'
        f'{html.escape(item.name)}</option>'
        for item in files
    )
    selected_url = f"/pdf/{quote(selected)}" if selected else ""
    viewer = (
        f'<iframe title="PDF viewer" src="{selected_url}"></iframe>'
        if selected
        else '<div class="empty">Upload a PDF to start reading.</div>'
    )
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    editor = ""
    if selected:
        safe_selected = html.escape(selected, quote=True)
        editor = f"""
        <aside>
          <a class="download" href="{selected_url}" download="{safe_selected}">Download this PDF</a>
          <form class="edit-form" method="post" action="/edit">
            <input type="hidden" name="file" value="{safe_selected}">
            <h2>Replace text</h2>
            <label>Find exact text<textarea name="find" rows="2"></textarea></label>
            <label>Replace with<textarea name="replace" rows="2"></textarea></label>
            <p class="hint">All matches are replaced using the original text's size, color, baseline,
              and closest available font style. Leave both boxes empty to only add text.</p>
            <h2>Add text</h2>
            <label>Text<textarea name="overlay" rows="2"></textarea></label>
            <label>Match style from existing text<input name="match_style" type="text"
              placeholder="Example: Admin Staff"></label>
            <div class="row">
              <label>Page<input name="page" type="number" min="1" value="1"></label>
              <label>X<input name="x" type="number" min="0" step="1" value="72"></label>
              <label>Y<input name="y" type="number" min="0" step="1" value="72"></label>
            </div>
            <label>Font size (when not matching)<input name="size" type="number" min="4" max="96" step="1" value="12"></label>
            <p class="hint">To make added text look identical, enter a visible piece of text whose
              style should be copied. X and Y are PDF points from the page's top-left.</p>
            <button type="submit">Create edited PDF</button>
          </form>
        </aside>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDF Viewer</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #16181d; color: #f4f5f7; }}
    header {{ display: flex; flex-wrap: wrap; align-items: center; gap: .75rem;
      padding: .8rem 1rem; background: #22262e; border-bottom: 1px solid #3a404b; }}
    h1 {{ margin: 0 auto 0 0; font-size: 1.15rem; }}
    form {{ display: flex; align-items: center; gap: .5rem; }}
    input, select, button {{ font: inherit; }}
    select, button, .upload {{ border: 1px solid #596273; border-radius: .45rem;
      padding: .45rem .7rem; background: #303641; color: white; }}
    button, .upload {{ cursor: pointer; }}
    .upload input {{ position: absolute; width: 1px; height: 1px; opacity: 0; }}
    main {{ display: flex; height: calc(100vh - 62px); }}
    iframe {{ flex: 1; min-width: 0; height: 100%; border: 0; background: white; }}
    aside {{ width: 310px; overflow: auto; padding: 1rem; background: #22262e;
      border-left: 1px solid #3a404b; }}
    aside form {{ display: block; }}
    aside h2 {{ margin: 1rem 0 .55rem; font-size: 1rem; }}
    aside label {{ display: block; margin: .55rem 0; font-size: .85rem; }}
    aside textarea, aside input {{ display: block; width: 100%; margin-top: .25rem; padding: .45rem;
      color: white; background: #171a20; border: 1px solid #596273; border-radius: .35rem; }}
    aside textarea {{ resize: vertical; }}
    .row {{ display: flex; gap: .5rem; }} .row label {{ flex: 1; }}
    .hint {{ color: #aeb6c4; font-size: .76rem; line-height: 1.35; }}
    .download {{ display: block; padding: .55rem; color: white; text-align: center;
      text-decoration: none; background: #1769d2; border-radius: .4rem; }}
    aside button {{ width: 100%; margin-top: .6rem; background: #1769d2; border-color: #3182e8; }}
    .empty {{ display: grid; place-items: center; height: 100%; color: #aeb6c4; }}
    .notice {{ width: 100%; margin: 0; color: #ffcf70; font-size: .9rem; }}
    @media (max-width: 680px) {{ header {{ align-items: stretch; }} form {{ width: 100%; }}
      select {{ min-width: 0; flex: 1; }} main {{ height: auto; flex-direction: column; }}
      iframe {{ width: 100%; height: 65vh; flex: none; }} aside {{ width: 100%; border: 0; }} }}
  </style>
</head>
<body>
  <header>
    <h1>PDF Viewer</h1>
    <form method="get" action="/">
      <select name="file" aria-label="Choose a PDF" onchange="this.form.submit()">
        <option value="">Choose a PDF...</option>{options}
      </select>
      <noscript><button type="submit">Open</button></noscript>
    </form>
    <form method="post" action="/upload" enctype="multipart/form-data">
      <label class="upload">Upload PDF<input name="pdf" type="file" accept="application/pdf,.pdf"
        required onchange="this.form.submit()"></label>
    </form>
    {notice}
  </header>
  <main>{viewer}{editor}</main>
</body>
</html>""".encode("utf-8")


class PDFHandler(BaseHTTPRequestHandler):
    def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            selected = parse_qs(parsed.query).get("file", [""])[0]
            if selected and not (UPLOAD_DIR / Path(selected).name).is_file():
                selected = ""
            self.send_bytes(page(selected=selected), "text/html; charset=utf-8")
            return

        if parsed.path.startswith("/pdf/"):
            filename = Path(unquote(parsed.path.removeprefix("/pdf/"))).name
            pdf_path = UPLOAD_DIR / filename
            if not filename.lower().endswith(".pdf") or not pdf_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "PDF not found")
                return
            content = pdf_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/pdf")
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(filename)}")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/edit":
            self.edit_pdf()
            return
        if route != "/upload":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
            self.send_bytes(page("File is empty or larger than 50 MB."), "text/html; charset=utf-8", 400)
            return

        content_type = self.headers.get("Content-Type", "")
        if "boundary=" not in content_type:
            self.send_bytes(page("Invalid upload."), "text/html; charset=utf-8", 400)
            return

        boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
        body = self.rfile.read(content_length)
        marker = b"--" + boundary
        uploaded_name = ""
        uploaded_data = b""
        for part in body.split(marker):
            headers, separator, data = part.partition(b"\r\n\r\n")
            if not separator or b'name="pdf"' not in headers:
                continue
            match = re.search(br'filename="([^"]*)"', headers)
            if match:
                uploaded_name = os.fsdecode(match.group(1))
                uploaded_data = data.removesuffix(b"\r\n").removesuffix(b"--")
                break

        if not uploaded_name.lower().endswith(".pdf") or not uploaded_data.startswith(b"%PDF-"):
            self.send_bytes(page("Please choose a valid PDF file."), "text/html; charset=utf-8", 400)
            return

        target = unique_pdf_path(uploaded_name)
        target.write_bytes(uploaded_data)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?file={quote(target.name)}")
        self.end_headers()

    def edit_pdf(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 1024 * 1024:
            self.send_bytes(page("Invalid edit request."), "text/html; charset=utf-8", 400)
            return
        values = parse_qs(self.rfile.read(content_length).decode("utf-8"), keep_blank_values=True)
        source_name = Path(values.get("file", [""])[0]).name
        source = UPLOAD_DIR / source_name
        if not source.is_file() or not source_name.lower().endswith(".pdf"):
            self.send_bytes(page("The source PDF was not found."), "text/html; charset=utf-8", 404)
            return
        if fitz is None:
            self.send_bytes(page("Editing requires PyMuPDF. Run: pip install -r requirements.txt", source_name),
                            "text/html; charset=utf-8", 500)
            return

        find_text = values.get("find", [""])[0]
        replacement = values.get("replace", [""])[0]
        overlay = values.get("overlay", [""])[0]
        match_style = values.get("match_style", [""])[0]
        if not find_text and not overlay:
            self.send_bytes(page("Enter text to replace or add.", source_name), "text/html; charset=utf-8", 400)
            return

        try:
            page_number = int(values.get("page", ["1"])[0])
            x = float(values.get("x", ["72"])[0])
            y = float(values.get("y", ["72"])[0])
            font_size = float(values.get("size", ["12"])[0])
            if x < 0 or y < 0 or not 4 <= font_size <= 96:
                raise ValueError

            document = fitz.open(source)
            replacements = 0
            if find_text:
                for pdf_page in document:
                    matches = pdf_page.search_for(find_text)
                    styled_matches = []
                    for rectangle in matches:
                        style = style_for_match(pdf_page, rectangle)
                        style["write_font"] = reusable_font(document, pdf_page, style)
                        styled_matches.append((rectangle, style))
                    for rectangle, _style in styled_matches:
                        pdf_page.add_redact_annot(rectangle, fill=None)
                    if matches:
                        pdf_page.apply_redactions()
                        for rectangle, style in styled_matches:
                            origin = style.get("origin", (rectangle.x0, rectangle.y1))
                            write_styled_text(
                                pdf_page, (rectangle.x0, float(origin[1])), replacement,
                                style["write_font"], float(style.get("size", 12)),
                                pdf_color(int(style.get("color", 0))),
                                float(style.get("alpha", 255)) / 255,
                            )
                        replacements += len(matches)
            if overlay:
                if not 1 <= page_number <= document.page_count:
                    raise ValueError
                overlay_page = document[page_number - 1]
                overlay_style = None
                if match_style:
                    style_matches = overlay_page.search_for(match_style)
                    if not style_matches:
                        raise ValueError(f'Style reference "{match_style}" was not found on page {page_number}.')
                    overlay_style = style_for_match(overlay_page, style_matches[0])
                if overlay_style:
                    overlay_font = reusable_font(document, overlay_page, overlay_style)
                    write_styled_text(
                        overlay_page, (x, y), overlay, overlay_font,
                        float(overlay_style.get("size", font_size)),
                        pdf_color(int(overlay_style.get("color", 0))),
                        float(overlay_style.get("alpha", 255)) / 255,
                    )
                else:
                    overlay_page.insert_text((x, y), overlay, fontsize=font_size,
                                             fontname="helv", color=(0, 0, 0), overlay=True)

            target = unique_pdf_path(f"edited_{source.stem}.pdf")
            document.save(target, garbage=4, deflate=True)
            document.close()
        except Exception as error:
            self.send_bytes(page(f"Could not edit PDF: {error}", source_name), "text/html; charset=utf-8", 400)
            return

        details = []
        if find_text:
            details.append(f"replaced {replacements} match(es)")
        if overlay:
            details.append(f"added text on page {page_number}")
        query = urlencode({"file": target.name})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?{query}")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local PDF viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    UPLOAD_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), PDFHandler)
    print(f"PDF Viewer is running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
