# PDF Viewer

A small local web application for uploading, viewing, editing, and downloading PDF files.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:8000> in a browser. Click **Upload PDF** and choose a
file. Uploaded and edited files are kept in the automatically created `uploads` folder.

The editor can replace every occurrence of exact text or add new text at an X/Y
position on a selected page. After applying an edit, preview the new file and use
**Download this PDF** to save it. Replacements preserve the original embedded font
(when reusable), size, color, opacity, baseline, and bold/italic/serif style. If a
PDF does not include reusable font data, the editor uses the closest compatible font.
For newly added text, fill in **Match style from existing text** with text already on
that page (for example, `Admin Staff`) to copy its complete appearance.

The default upload limit is 50 MB. To use another port:

```powershell
python app.py --port 8080
```
