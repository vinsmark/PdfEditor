"""Inline Streamlit component for editing positioned PDF text boxes."""

import streamlit as st


VISUAL_EDITOR_HTML = """
<div class="visual-editor">
  <div class="editor-help">Click existing text to edit it. Add text, then drag it with its move handle.</div>
  <div class="format-toolbar" hidden>
    <strong>Added text</strong>
    <label>Size <input class="size-input" type="number" min="4" max="96" step="1"></label>
    <label>Color <input class="color-input" type="color"></label>
    <button class="bold-button format-button" type="button" title="Bold"><b>B</b></button>
    <button class="italic-button format-button" type="button" title="Italic"><i>I</i></button>
    <button class="delete-button" type="button">Delete added text</button>
  </div>
  <div class="page-stage">
    <img class="page-image" alt="PDF page background">
    <div class="text-layer"></div>
  </div>
  <div class="editor-actions">
    <button class="add-button" type="button">Add text</button>
    <button class="apply-button" type="button">Apply page changes</button>
  </div>
</div>
"""

VISUAL_EDITOR_CSS = """
.visual-editor { width: 100%; font-family: var(--st-font); }

.editor-help { margin-bottom: .7rem; color: #16181c; opacity: .8; font-size: .9rem; font-weight: 500; }

.format-toolbar { position: sticky; top: 0; z-index: 30; align-items: center; justify-content: flex-start;
                  gap: .65rem; margin: 0 0 .7rem; width: 100%; padding: .55rem .7rem;
                  color: #111111; background: #ffffff; flex-wrap: wrap;
                  border: 1px solid #d6d6d6; border-radius: 6px; }
.format-toolbar:not([hidden]) { display: flex; }
.format-toolbar strong { font-size: .85rem; font-weight: 700; color: #111111; }
.format-toolbar label { display: flex; align-items: center; gap: .35rem; font-size: .85rem;
                        font-weight: 500; color: #2c2f34; }

.size-input { width: 4.5rem; padding: .3rem .4rem; border: 1px solid #b8b8b8; border-radius: 6px;
              background: #ffffff; color: #111111; font-weight: 600; }
.color-input { width: 2.4rem; height: 2rem; padding: 0; border: 1px solid #b8b8b8; border-radius: 6px;
               background: #ffffff; cursor: pointer; }

.format-button { width: 2.1rem; height: 2rem; border: 1px solid #111111; border-radius: 6px;
                 color: #111111; background: #ffffff; cursor: pointer; font-weight: 700; }
.format-button:hover { background: #f2f2f2; }
.format-button.active { color: #ffffff; background: #111111; }

.delete-button { padding: .42rem .7rem; border: 1.5px solid #b3272c; border-radius: 6px;
                 color: #b3272c; background: #ffffff; cursor: pointer;
                 font-size: .85rem; font-weight: 600; }
.delete-button:hover { color: #ffffff; background: #b3272c; }

.page-stage { position: relative; width: 100%; margin: 0 auto; overflow: hidden; background: white;
              container-type: size; border: 1px solid #d6d6d6; box-shadow: 0 2px 12px rgba(0,0,0,.12); }
.page-image { display: block; width: 100%; height: auto; }
.text-layer { position: absolute; inset: 0; }
.pdf-box { position: absolute; min-width: 3px; min-height: 6px; margin: 0; transform-origin: left bottom; }
.pdf-text { width: 100%; height: 100%; min-width: inherit; min-height: inherit; padding: 0 1px; margin: 0;
            overflow: visible; white-space: pre; color: transparent; line-height: 1;
            font-family: var(--box-family); font-size: var(--box-size); font-weight: var(--box-weight);
            font-style: var(--box-style); outline: none; background: transparent; cursor: text; }
.pdf-box:hover .pdf-text, .pdf-text:focus { color: var(--box-color); outline: 1px solid #666;
                                         z-index: 10; background: white; }
.pdf-box.changed .pdf-text { color: var(--box-color); outline: 1px solid #111111; background: #f2f2f2; }

.drag-handle { position: absolute; left: -22px; top: 50%; transform: translateY(-50%); display: grid;
               place-items: center; width: 20px; height: 20px; border-radius: 50%; color: white;
               background: #404040; cursor: move; user-select: none; font: 700 13px sans-serif; z-index: 15; }
.page-stage.add-mode { cursor: crosshair; }

.editor-actions { position: sticky; bottom: 0; display: flex; justify-content: flex-end; gap: .55rem;
                  padding: .8rem 0 .2rem; background: #eeeeee; z-index: 20; }
.apply-button, .add-button { min-height: 44px; border-radius: 6px; padding: .6rem 1.1rem;
                             font-size: 14px; font-weight: 600; cursor: pointer; transition: .15s ease; }
.apply-button { color: #ffffff; background: #111111; border: 1px solid #111111; }
.apply-button:hover:not(:disabled) { background: #303030; border-color: #303030; }
.add-button { color: #111111; background: #ffffff; border: 1px solid #111111; }
.add-button:hover { background: #f2f2f2; }
.add-button.active { color: #ffffff; background: #111111; border-color: #111111; }
.apply-button:disabled { opacity: .5; cursor: default; }

@media (max-width: 900px) {
  .editor-actions { justify-content: stretch; }
  .apply-button, .add-button { flex: 1; }
}
"""

VISUAL_EDITOR_JS = """
export default function(component) {
  const { data, parentElement, setStateValue } = component;
  const stage = parentElement.querySelector('.page-stage');
  const image = parentElement.querySelector('.page-image');
  const layer = parentElement.querySelector('.text-layer');
  const applyButton = parentElement.querySelector('.apply-button');
  const addButton = parentElement.querySelector('.add-button');
  const toolbar = parentElement.querySelector('.format-toolbar');
  const sizeInput = parentElement.querySelector('.size-input');
  const colorInput = parentElement.querySelector('.color-input');
  const boldButton = parentElement.querySelector('.bold-button');
  const italicButton = parentElement.querySelector('.italic-button');
  const deleteButton = parentElement.querySelector('.delete-button');
  let addMode = false;
  let additionNumber = 0;
  let selectedBox = null;

  image.src = `data:image/png;base64,${data.image}`;
  stage.style.aspectRatio = `${data.width} / ${data.height}`;
  layer.replaceChildren();

  function textValue(box) {
    return box.querySelector('.pdf-text').innerText.replace(/\\r/g, '');
  }

  function updateBoxStyle(box) {
    box.style.setProperty('--box-size', `${Number(box.dataset.size) * 100 / data.height}cqh`);
    box.style.setProperty('--box-color', box.dataset.color);
    box.style.setProperty('--box-weight', box.dataset.bold === 'true' ? '700' : '400');
    box.style.setProperty('--box-style', box.dataset.italic === 'true' ? 'italic' : 'normal');
  }

  function selectAddedBox(box) {
    if (box.dataset.new !== 'true') return;
    selectedBox = box;
    toolbar.hidden = false;
    sizeInput.value = box.dataset.size;
    colorInput.value = box.dataset.color;
    boldButton.classList.toggle('active', box.dataset.bold === 'true');
    italicButton.classList.toggle('active', box.dataset.italic === 'true');
  }

  function installDrag(handle, box) {
    handle.onpointerdown = (event) => {
      event.preventDefault();
      selectAddedBox(box);
      handle.setPointerCapture(event.pointerId);
      const stageBounds = stage.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const originalX = Number(box.dataset.x);
      const originalY = Number(box.dataset.y);
      handle.onpointermove = (moveEvent) => {
        if (!handle.hasPointerCapture(moveEvent.pointerId)) return;
        const x = Math.max(0, Math.min(data.width, originalX + (moveEvent.clientX - startX) * data.width / stageBounds.width));
        const y = Math.max(0, Math.min(data.height, originalY + (moveEvent.clientY - startY) * data.height / stageBounds.height));
        box.dataset.x = x;
        box.dataset.y = y;
        box.style.left = `${x * 100 / data.width}%`;
        box.style.top = `${y * 100 / data.height}%`;
      };
    };
  }

  function mountBox(box, isNew = false) {
    const wrapper = document.createElement('div');
    wrapper.className = isNew ? 'pdf-box changed' : 'pdf-box';
    wrapper.dataset.id = box.id;
    wrapper.dataset.original = isNew ? '' : box.text;
    wrapper.dataset.new = isNew ? 'true' : 'false';
    wrapper.dataset.x = box.x;
    wrapper.dataset.y = box.y;
    wrapper.dataset.reference = box.reference || box.id;
    wrapper.dataset.size = box.size;
    wrapper.dataset.color = box.color;
    wrapper.dataset.bold = String(Boolean(box.bold));
    wrapper.dataset.italic = String(Boolean(box.italic));
    wrapper.style.left = `${box.x * 100 / data.width}%`;
    wrapper.style.top = `${box.y * 100 / data.height}%`;
    wrapper.style.width = `${Math.max(box.width * 100 / data.width, 0.25)}%`;
    wrapper.style.height = `${Math.max(box.height * 100 / data.height, 0.5)}%`;
    wrapper.style.setProperty('--box-family', box.family);
    if (box.angle) wrapper.style.transform = `rotate(${box.angle}deg)`;
    updateBoxStyle(wrapper);

    const text = document.createElement('div');
    text.className = 'pdf-text';
    text.contentEditable = 'plaintext-only';
    text.spellcheck = false;
    text.textContent = box.text;
    text.oninput = () => wrapper.classList.toggle('changed', isNew || textValue(wrapper) !== wrapper.dataset.original);
    text.onclick = () => selectAddedBox(wrapper);
    wrapper.appendChild(text);

    if (isNew) {
      const handle = document.createElement('span');
      handle.className = 'drag-handle';
      handle.textContent = '+';
      handle.title = 'Drag to move';
      wrapper.appendChild(handle);
      installDrag(handle, wrapper);
    }
    layer.appendChild(wrapper);
    return wrapper;
  }

  for (const box of data.boxes) mountBox(box);

  sizeInput.oninput = () => {
    if (!selectedBox) return;
    selectedBox.dataset.size = String(Math.max(4, Math.min(96, Number(sizeInput.value) || 12)));
    updateBoxStyle(selectedBox);
  };
  colorInput.oninput = () => {
    if (!selectedBox) return;
    selectedBox.dataset.color = colorInput.value;
    updateBoxStyle(selectedBox);
  };
  boldButton.onclick = () => {
    if (!selectedBox) return;
    selectedBox.dataset.bold = String(selectedBox.dataset.bold !== 'true');
    updateBoxStyle(selectedBox);
    selectAddedBox(selectedBox);
  };
  italicButton.onclick = () => {
    if (!selectedBox) return;
    selectedBox.dataset.italic = String(selectedBox.dataset.italic !== 'true');
    updateBoxStyle(selectedBox);
    selectAddedBox(selectedBox);
  };
  deleteButton.onclick = () => {
    if (!selectedBox || selectedBox.dataset.new !== 'true') return;
    selectedBox.remove();
    selectedBox = null;
    toolbar.hidden = true;
  };

  addButton.onclick = () => {
    addMode = !addMode;
    addButton.classList.toggle('active', addMode);
    addButton.textContent = addMode ? 'Click page location' : 'Add text';
    stage.classList.toggle('add-mode', addMode);
  };

  stage.onclick = (event) => {
    if (!addMode || event.target.closest('.pdf-box')) return;
    const bounds = stage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * data.width / bounds.width;
    const y = (event.clientY - bounds.top) * data.height / bounds.height;
    const reference = data.boxes.reduce((nearest, box) => {
      const distance = Math.hypot((box.x + box.width / 2) - x, (box.y + box.height / 2) - y);
      return !nearest || distance < nearest.distance ? { box, distance } : nearest;
    }, null)?.box;
    if (!reference) return;
    const newBox = { ...reference, id: `new-${additionNumber++}`, text: '', x, y,
                     width: Math.max(reference.width, 90), reference: reference.id };
    const wrapper = mountBox(newBox, true);
    addMode = false;
    addButton.classList.remove('active');
    addButton.textContent = 'Add text';
    stage.classList.remove('add-mode');
    selectAddedBox(wrapper);
    wrapper.querySelector('.pdf-text').focus();
  };

  applyButton.onclick = () => {
    const boxes = [...layer.querySelectorAll('.pdf-box')];
    const changes = boxes
      .filter(box => box.dataset.new !== 'true' && textValue(box) !== box.dataset.original)
      .map(box => ({ id: box.dataset.id, text: textValue(box) }));
    const additions = boxes
      .filter(box => box.dataset.new === 'true' && textValue(box).trim())
      .map(box => ({ text: textValue(box), x: Number(box.dataset.x), y: Number(box.dataset.y),
                     reference: box.dataset.reference, size: Number(box.dataset.size),
                     color: box.dataset.color, bold: box.dataset.bold === 'true',
                     italic: box.dataset.italic === 'true' }));
    if (!changes.length && !additions.length) return;
    applyButton.disabled = true;
    setStateValue('edits', JSON.stringify({ changes, additions }));
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