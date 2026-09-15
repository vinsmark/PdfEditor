"""Inline Streamlit component for editing positioned PDF text boxes."""

import streamlit as st


VISUAL_EDITOR_HTML = """
<div class="visual-editor">
  <div class="editor-help">Move over text, click it, and type. Only the active or changed text is outlined.</div>
  <div class="page-stage">
    <img class="page-image" alt="PDF page background">
    <div class="text-layer"></div>
  </div>
  <div class="editor-actions">
    <button class="apply-button" type="button">Apply page changes</button>
  </div>
</div>
"""

VISUAL_EDITOR_CSS = """
.visual-editor { width: 100%; font-family: var(--st-font); }
.editor-help { margin-bottom: .6rem; color: var(--st-text-color); opacity: .75; font-size: .9rem; }
.page-stage { position: relative; width: 78%; margin: 0 auto; overflow: hidden; background: white; container-type: size;
              border: 1px solid rgba(128,128,128,.45); box-shadow: 0 2px 12px rgba(0,0,0,.18); }
.page-image { display: block; width: 100%; height: auto; }
.text-layer { position: absolute; inset: 0; }
.pdf-text { position: absolute; min-width: 3px; min-height: 6px; padding: 0 1px; margin: 0;
            overflow: visible; white-space: pre; color: transparent; line-height: 1;
            font-family: var(--box-family); font-size: var(--box-size); font-weight: var(--box-weight);
            font-style: var(--box-style); transform-origin: left bottom; outline: none;
            background: transparent; cursor: text; }
.pdf-text:hover, .pdf-text:focus { color: var(--box-color); outline: 1px solid #666;
                                  z-index: 10; background: white; }
.pdf-text.changed { color: var(--box-color); outline: 1px solid #ff8a00; background: #fff8e8; }
.editor-actions { position: sticky; bottom: 0; display: flex; justify-content: flex-end;
                  padding: .75rem 0; background: var(--st-background-color); z-index: 20; }
.apply-button { border: 0; border-radius: .45rem; padding: .65rem 1rem; color: white;
                background: var(--st-primary-color); cursor: pointer; font-weight: 600; }
.apply-button:disabled { opacity: .55; cursor: default; }
@media (max-width: 900px) { .page-stage { width: 100%; } }
"""

VISUAL_EDITOR_JS = """
export default function(component) {
  const { data, parentElement, setStateValue } = component;
  const stage = parentElement.querySelector('.page-stage');
  const image = parentElement.querySelector('.page-image');
  const layer = parentElement.querySelector('.text-layer');
  const applyButton = parentElement.querySelector('.apply-button');

  image.src = `data:image/png;base64,${data.image}`;
  stage.style.aspectRatio = `${data.width} / ${data.height}`;
  layer.replaceChildren();

  for (const box of data.boxes) {
    const element = document.createElement('div');
    element.className = 'pdf-text';
    element.contentEditable = 'plaintext-only';
    element.spellcheck = false;
    element.dataset.id = box.id;
    element.dataset.original = box.text;
    element.textContent = box.text;
    element.style.left = `${box.x * 100 / data.width}%`;
    element.style.top = `${box.y * 100 / data.height}%`;
    element.style.width = `${Math.max(box.width * 100 / data.width, 0.25)}%`;
    element.style.height = `${Math.max(box.height * 100 / data.height, 0.5)}%`;
    element.style.setProperty('--box-size', `${box.size * 100 / data.height}cqh`);
    element.style.setProperty('--box-color', box.color);
    element.style.setProperty('--box-family', box.family);
    element.style.setProperty('--box-weight', box.bold ? '700' : '400');
    element.style.setProperty('--box-style', box.italic ? 'italic' : 'normal');
    if (box.angle) element.style.transform = `rotate(${box.angle}deg)`;
    element.oninput = () => element.classList.toggle('changed', element.innerText !== element.dataset.original);
    layer.appendChild(element);
  }

  applyButton.onclick = () => {
    const edits = [...layer.querySelectorAll('.pdf-text')]
      .filter(element => element.innerText !== element.dataset.original)
      .map(element => ({ id: element.dataset.id, text: element.innerText.replace(/\\r/g, '') }));
    if (!edits.length) return;
    applyButton.disabled = true;
    setStateValue('edits', JSON.stringify(edits));
  };
}
"""

visual_editor_component = st.components.v2.component(
    "visual_pdf_text_editor",
    html=VISUAL_EDITOR_HTML,
    css=VISUAL_EDITOR_CSS,
    js=VISUAL_EDITOR_JS,
)


def visual_editor(data: dict, key: str):
    """Mount the visual editor and return its component result."""
    return visual_editor_component(
        data=data,
        default={"edits": ""},
        key=key,
        on_edits_change=lambda: None,
    )
