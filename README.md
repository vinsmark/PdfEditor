# Private PDF Editor

A Streamlit web application for viewing, editing, and downloading PDFs. Uploaded
and edited documents are kept only in the current Streamlit session's memory. The
application does not write PDFs to the project directory, a database, or cloud storage.

## Features

- Replace every occurrence of exact searchable text.
- Preserve the original embedded font, size, color, opacity, and baseline when possible.
- Add positioned text and copy styling from existing text on the same page.
- Click **Edit PDF** to edit detected text directly in positioned page boxes.
- Preview the current in-memory PDF after every edit.
- Download the latest revision and explicitly clear it from memory.
- Replace the previous in-memory revision after each successful edit.

Scanned image-only PDFs require OCR and cannot be text-edited by this version.
Password-protected PDFs are not supported.

## Run locally

Python 3.12 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens the app at <http://localhost:8501>.

## Deploy on Streamlit Community Cloud

1. Push `app.py`, `requirements.txt`, `.gitignore`, and `.streamlit/config.toml` to GitHub.
2. Do not commit the `uploads` directory or any PDF documents.
3. At <https://share.streamlit.io>, create an app from the repository.
4. Select `app.py` as the entrypoint and deploy.

When deployed, a PDF is transmitted to the Streamlit server and temporarily held in
that session's RAM for processing. It is not permanently stored by this application.
For documents that must never leave the user's device, a browser-only application is required.
