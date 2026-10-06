"""
template_handler.py
────────────────────
Certificate template loader and drag-and-drop field-placement editor.

Architecture
───────────────
• A tiny background HTTP server (bridge) receives layout JSON from the
  JavaScript canvas via fetch() and writes it to a local file.
• Python reads that file on every Streamlit rerun, so the canvas changes
  are always reflected in the PIL preview and downstream steps.
• The HTML5 <canvas> renders actual text with the chosen font, colour, and
  size — giving a true real-time preview inside the editor.
"""

import base64
import http.server
import io
import json
import threading
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageFont

# ─── Paths ────────────────────────────────────────────────────────────────────

ASSETS_DIR       = Path(__file__).parent / "assets"
FONTS_DIR        = ASSETS_DIR / "fonts"
_BRIDGE_FILE     = Path(__file__).parent / ".layout_bridge.json"

DEFAULT_LAYOUT: list[dict] = []

# ─── Font catalogue ───────────────────────────────────────────────────────────

FONT_CATALOGUE: dict[str, tuple[str, str]] = {
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

FONT_NAMES = list(FONT_CATALOGUE.keys())

_FALLBACK_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FALLBACK_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _get_font(size: int, bold: bool = False, family: str = "Arial") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    reg, bd = FONT_CATALOGUE.get(family, FONT_CATALOGUE["Arial"])
    primary   = bd if bold else reg
    secondary = _FALLBACK_BOLD if bold else _FALLBACK_REG
    for p in (primary, secondary, "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


# ─── Bridge server ────────────────────────────────────────────────────────────
# Tiny HTTP server that receives layout JSON from the canvas JS via fetch()
# and writes it to _BRIDGE_FILE so Python can read it on the next rerun.

_BRIDGE_PORT = 8765
_bridge_ready = False


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            layout = json.loads(body)
            _BRIDGE_FILE.write_text(json.dumps(layout), encoding="utf-8")
        except Exception:
            pass
        self._cors()
        self.end_headers()
        self.wfile.write(b"ok")

    def _cors(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):  # silence console spam
        pass


def _start_bridge():
    global _bridge_ready
    if _bridge_ready:
        return
    try:
        srv = http.server.HTTPServer(("127.0.0.1", _BRIDGE_PORT), _BridgeHandler)
        t   = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        _bridge_ready = True
    except OSError:
        # Port already in use — bridge from a previous process run is still alive.
        _bridge_ready = True


_start_bridge()


def _read_bridge() -> list | None:
    try:
        if _BRIDGE_FILE.exists():
            return json.loads(_BRIDGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_bridge(layout: list) -> None:
    try:
        _BRIDGE_FILE.write_text(json.dumps(layout), encoding="utf-8")
    except Exception:
        pass


# ─── Template loading ─────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_template_from_bytes(raw: bytes) -> Image.Image:
    return Image.open(io.BytesIO(raw)).convert("RGB")




# ─── PIL preview renderer ─────────────────────────────────────────────────────

def render_preview(
    template: Image.Image,
    layout: list[dict],
    sample_data: dict | None = None,
) -> Image.Image:
    img  = template.copy()
    draw = ImageDraw.Draw(img)
    W, H = img.size

    for field in layout:
        text = field.get("placeholder", "")
        # Always try to replace placeholders with actual data if available
        if sample_data and not field.get("static", False):
            for key, val in sample_data.items():
                text = text.replace(f"{{{key}}}", str(val))
        # If no sample_data provided, extract field name from placeholder and use as example
        elif not sample_data and not field.get("static", False):
            # Extract field name from placeholder like {Name} -> Name
            placeholder = field.get("placeholder", "")
            if placeholder.startswith("{") and placeholder.endswith("}"):
                field_name = placeholder[1:-1]
                text = f"Sample {field_name}"

        x     = int(field["x_pct"] / 100 * W)
        y     = int(field["y_pct"] / 100 * H)
        font  = _get_font(int(field.get("font_size", 26)),
                          bool(field.get("bold", False)),
                          str(field.get("font_family", "Arial")))
        color = field.get("font_color", "#000000")
        align = field.get("align", "center")

        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw   = bbox[2] - bbox[0]
            th   = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(text, font=font)

        # Calculate box dimensions
        box_width = tw + 16  # 8px padding each side
        box_height = th + 8  # 4px padding top/bottom
        
        # Calculate box position based on alignment
        if align == "center":
            box_x = x - box_width // 2
        elif align == "right":
            box_x = x - box_width
        else:  # left
            box_x = x
        
        box_y = y - th // 2 - 4  # Center vertically with padding
        
        # Draw white border (no background)
        draw.rectangle([box_x, box_y, box_x + box_width, box_y + box_height], 
                      outline="#ffffff", width=1)
        
        # Draw text centered in box
        center_x = box_x + box_width // 2
        center_y = box_y + box_height // 2
        
        # Use actual font color for visibility
        text_color = color if color and color != "#000000" else "#ffffff"
        shadow_color = "#00000055"
        
        draw.text((center_x + 1, center_y + 1), text, font=font, fill=shadow_color, 
                 anchor="mm")
        draw.text((center_x, center_y), text, font=font, fill=text_color, 
                 anchor="mm")

    return img


# ─── Canvas HTML ──────────────────────────────────────────────────────────────

def _pil_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _canvas_html(img_b64: str, layout: list[dict], font_names: list[str], port: int) -> str:
    layout_json = json.dumps(layout)
    font_opts   = "".join(f'<option value="{n}">{n}</option>' for n in font_names)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px;
    background: #09090b;
    color: #e4e4e7;
    overflow: hidden;
  }}
  #app {{ display: flex; height: 100vh; }}

  /* ── Canvas area ── */
  #canvas-area {{
    flex: 1;
    min-width: 0;
    background: #111113;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    position: relative;
  }}
  #cert {{
    display: block;
    max-width: 100%;
    max-height: 100%;
    cursor: default;
  }}
  #hint {{
    position: absolute;
    bottom: 10px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(9,9,11,0.75);
    color: #71717a;
    font-size: 11px;
    padding: 4px 12px;
    border-radius: 4px;
    pointer-events: none;
    white-space: nowrap;
  }}

  /* ── Side panel ── */
  #panel {{
    width: 256px;
    flex-shrink: 0;
    background: #111113;
    border-left: 1px solid #1f1f23;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  /* Sections */
  .sec {{
    padding: 14px 14px 10px;
    border-bottom: 1px solid #1f1f23;
  }}
  .sec-title {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #52525b;
    margin-bottom: 10px;
  }}

  /* Field rows */
  .frow {{
    display: flex;
    align-items: center;
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    color: #a1a1aa;
    font-size: 11px;
    margin-bottom: 0;
    gap: 6px;
    border: 1px solid transparent;
    min-width: fit-content;
  }}
  .frow:hover  {{ background: #18181b; color: #e4e4e7; }}
  .frow.active {{ background: #1c2033; color: #93c5fd; }}
  .frow-dot {{
    width: 6px; height: 6px; border-radius: 50%;
    background: #3f3f46; flex-shrink: 0;
  }}
  .frow.active .frow-dot {{ background: #3b82f6; }}

  /* Horizontal field list */
  #field-list {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 4px 0;
  }}

  /* Property form */
  #props-wrap {{ flex: 1; overflow-y: auto; }}
  #empty-msg {{
    padding: 24px 14px;
    color: #3f3f46;
    font-size: 11px;
    line-height: 1.7;
    text-align: center;
  }}
  #props-form {{ padding: 14px; display: none; }}

  label.lbl {{
    display: block;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: #52525b;
    margin-bottom: 4px;
  }}
  .field-group {{ margin-bottom: 10px; }}
  .row2 {{ display: flex; gap: 8px; }}
  .row2 .field-group {{ flex: 1; min-width: 0; }}

  input[type=text],
  input[type=number],
  select {{
    width: 100%;
    background: #09090b;
    border: 1px solid #27272a;
    border-radius: 4px;
    color: #e4e4e7;
    font-size: 12px;
    padding: 5px 8px;
    outline: none;
  }}
  input:focus, select:focus {{ border-color: #3b82f6; }}
  input[type=color] {{
    width: 100%;
    height: 30px;
    padding: 2px 3px;
    background: #09090b;
    border: 1px solid #27272a;
    border-radius: 4px;
    cursor: pointer;
  }}

  .checks {{ display: flex; gap: 16px; margin-bottom: 10px; }}
  .chk {{ display: flex; align-items: center; gap: 6px; cursor: pointer; color: #a1a1aa; font-size: 12px; }}
  .chk input {{ accent-color: #3b82f6; cursor: pointer; }}

  .btn-remove {{
    width: 100%;
    padding: 6px;
    background: transparent;
    border: 1px solid #2d1515;
    border-radius: 4px;
    color: #f87171;
    font-size: 12px;
    cursor: pointer;
    margin-top: 4px;
  }}
  .btn-remove:hover {{ background: #1f0d0d; }}

  /* Saved flash */
  #saved {{
    font-size: 10px;
    color: #22c55e;
    text-align: center;
    padding: 6px 0 0;
    display: none;
  }}
</style>
</head>
<body>
<div id="app">

  <!-- Canvas -->
  <div id="canvas-area">
    <canvas id="cert"></canvas>
    <div id="hint">Click a field to select it · Drag to reposition</div>
  </div>

  <!-- Panel -->
  <div id="panel">
    <div class="sec">
      <div class="sec-title">Fields</div>
      <div id="field-list"></div>
    </div>

    <div id="props-wrap">
      <div id="empty-msg">Click a field on the canvas<br>to edit its properties</div>
      <div id="props-form">
        <div class="field-group">
          <label class="lbl">Text / Placeholder</label>
          <input id="p-ph" type="text" oninput="syncField()">
        </div>
        <div class="field-group">
          <label class="lbl">Font</label>
          <select id="p-font" onchange="syncField()">{font_opts}</select>
        </div>
        <div class="row2">
          <div class="field-group">
            <label class="lbl">Size</label>
            <input id="p-size" type="number" min="6" max="140" oninput="syncField()">
          </div>
          <div class="field-group">
            <label class="lbl">Colour</label>
            <input id="p-color" type="color" oninput="syncField()">
          </div>
        </div>
        <div class="field-group">
          <label class="lbl">Alignment</label>
          <select id="p-align" onchange="syncField()">
            <option value="center">Centre</option>
            <option value="left">Left</option>
            <option value="right">Right</option>
          </select>
        </div>
        <div class="checks">
          <label class="chk">
            <input id="p-bold" type="checkbox" onchange="syncField()"> Bold
          </label>
          <label class="chk">
            <input id="p-req" type="checkbox" onchange="syncField()"> Required
          </label>
        </div>
        <button class="btn-remove" onclick="removeField()">Remove field</button>
        <div id="saved">✓ Saved</div>
      </div>
    </div>
  </div>

</div>

<script>
/* ── State ─────────────────────────────────── */
let fields = {layout_json};
let sel    = null;
let saveTimer = null;
const BRIDGE  = 'http://127.0.0.1:{port}/';

/* ── Canvas setup ──────────────────────────── */
const canvas = document.getElementById('cert');
const ctx    = canvas.getContext('2d');
const bgImg  = new Image();

bgImg.onload = () => {{
  // Match canvas resolution to image
  canvas.width  = bgImg.naturalWidth;
  canvas.height = bgImg.naturalHeight;
  fitCanvas();
  redraw();
}};
bgImg.src = 'data:image/png;base64,{img_b64}';

window.addEventListener('resize', fitCanvas);

function fitCanvas() {{
  const area = document.getElementById('canvas-area');
  const maxW = area.clientWidth  - 16;
  const maxH = area.clientHeight - 16;
  const scaleW = maxW / canvas.width;
  const scaleH = maxH / canvas.height;
  const scale  = Math.min(scaleW, scaleH, 1);
  canvas.style.width  = (canvas.width  * scale) + 'px';
  canvas.style.height = (canvas.height * scale) + 'px';
}}

/* ── Drawing ────────────────────────────────── */
function redraw() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bgImg, 0, 0);

  fields.forEach((f, i) => {{
    const x = f.x_pct / 100 * canvas.width;
    const y = f.y_pct / 100 * canvas.height;
    const size   = f.font_size || 26;
    const weight = f.bold ? 'bold' : 'normal';
    const family = (f.font_family || 'Arial').replace(/"/g, '\\"');

    ctx.save();
    
    // Set font properties from field settings
    ctx.font = weight + ' ' + size + 'px "' + family + '", sans-serif';
    ctx.fillStyle = f.font_color || '#ffffff';
    ctx.textBaseline = 'alphabetic';
    
    // Get text dimensions
    const text = f.placeholder || '';
    const metrics = ctx.measureText(text);
    const textWidth = metrics.width;
    const textHeight = size;
    
    // Calculate text position based on alignment
    let textX = x;
    ctx.textAlign = f.align || 'center';
    
    // Calculate box position
    let boxX, boxY, boxWidth, boxHeight;
    boxY = y - textHeight / 2;
    boxWidth = textWidth + 16; // 8px padding on each side
    boxHeight = textHeight + 8; // 4px padding top/bottom
    
    if ((f.align || 'center') === 'center') {{
      boxX = x - boxWidth / 2;
    }} else if ((f.align || 'center') === 'right') {{
      boxX = x - boxWidth;
    }} else {{
      boxX = x;
    }}
    
    // Draw border only (no background)
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1;
    ctx.strokeRect(boxX, boxY, boxWidth, boxHeight);
    
    // Draw text centered in box
    const centerX = boxX + boxWidth / 2;
    const centerY = boxY + boxHeight / 2;
    
    // Temporarily set text alignment to center for box centering
    const originalAlign = ctx.textAlign;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    
    ctx.fillText(text, centerX, centerY);
    
    // Restore original alignment
    ctx.textAlign = originalAlign;
    ctx.textBaseline = 'alphabetic';
    
    ctx.restore();

    // Selection highlight - enhanced box
    if (i === sel) {{
      ctx.save();
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth   = 2 / (canvas.width / canvas.clientWidth || 1);
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(boxX - 2, boxY - 2, boxWidth + 4, boxHeight + 4);
      ctx.restore();
    }}
  }});
}}

/* ── Hit testing ───────────────────────────── */
function canvasXY(e) {{
  const r   = canvas.getBoundingClientRect();
  const scX = canvas.width  / r.width;
  const scY = canvas.height / r.height;
  return [
    (e.clientX - r.left) * scX,
    (e.clientY - r.top)  * scY,
  ];
}}

function hitField(mx, my) {{
  // Test in reverse (top fields first)
  for (let i = fields.length - 1; i >= 0; i--) {{
    const f  = fields[i];
    const fx = f.x_pct / 100 * canvas.width;
    const fy = f.y_pct / 100 * canvas.height;

    ctx.font = ((f.bold ? 'bold' : 'normal') + ' ' + (f.font_size || 26) +
      'px "' + (f.font_family || 'Arial') + '", sans-serif');
    ctx.textAlign    = f.align || 'center';
    ctx.textBaseline = 'middle';

    const tw  = ctx.measureText(f.placeholder || '').width;
    const th  = (f.font_size || 26) * 1.4;
    let   bx  = fx;
    if ((f.align || 'center') === 'center') bx -= tw / 2;
    if ((f.align || 'center') === 'right')  bx -= tw;

    const PAD = 10;
    if (mx >= bx - PAD && mx <= bx + tw + PAD &&
        my >= fy - th / 2 - PAD && my <= fy + th / 2 + PAD) {{
      return i;
    }}
  }}
  return null;
}}

/* ── Mouse events ──────────────────────────── */
let dragging = false;
let dragStart = null;

canvas.addEventListener('mousedown', e => {{
  const [mx, my] = canvasXY(e);
  const hit = hitField(mx, my);
  if (hit !== null) {{
    sel      = hit;
    dragging = true;
    dragStart = {{ mx, my, px: fields[hit].x_pct, py: fields[hit].y_pct }};
    canvas.style.cursor = 'grabbing';
    loadProps();
    redraw();
    renderList();
    e.preventDefault();
  }} else {{
    sel = null;
    showEmpty();
    redraw();
    renderList();
  }}
}});

window.addEventListener('mousemove', e => {{
  if (!dragging || sel === null) return;
  const [mx, my] = canvasXY(e);
  fields[sel].x_pct = +Math.max(0, Math.min(100,
    dragStart.px + (mx - dragStart.mx) / canvas.width  * 100)).toFixed(2);
  fields[sel].y_pct = +Math.max(0, Math.min(100,
    dragStart.py + (my - dragStart.my) / canvas.height * 100)).toFixed(2);
  redraw();
}});

window.addEventListener('mouseup', () => {{
  if (dragging) {{
    dragging = false;
    canvas.style.cursor = 'default';
    autoSave();
  }}
}});

// Change cursor on hover
canvas.addEventListener('mousemove', e => {{
  if (dragging) return;
  const [mx, my] = canvasXY(e);
  canvas.style.cursor = hitField(mx, my) !== null ? 'grab' : 'default';
}});

/* ── Field list ────────────────────────────── */
function renderList() {{
  const ul = document.getElementById('field-list');
  ul.innerHTML = '';
  if (!fields.length) {{
    ul.innerHTML = '<div style="color:#3f3f46;font-size:11px;padding:4px 0">No fields selected</div>';
    return;
  }}
  fields.forEach((f, i) => {{
    const row = document.createElement('div');
    row.className = 'frow' + (i === sel ? ' active' : '');
    row.innerHTML = '<span class="frow-dot"></span>' + esc(f.field_key);
    row.onclick   = () => {{ sel = i; loadProps(); redraw(); renderList(); }};
    ul.appendChild(row);
  }});
}}

/* ── Property form ─────────────────────────── */
function loadProps() {{
  if (sel === null) return;
  const f = fields[sel];
  document.getElementById('empty-msg').style.display   = 'none';
  document.getElementById('props-form').style.display  = 'block';
  document.getElementById('p-ph').value    = f.placeholder || '';
  document.getElementById('p-font').value  = f.font_family || 'Arial';
  document.getElementById('p-size').value  = f.font_size   || 26;
  document.getElementById('p-color').value = f.font_color  || '#000000';
  document.getElementById('p-align').value = f.align       || 'center';
  document.getElementById('p-bold').checked = !!f.bold;
  document.getElementById('p-req').checked  = !!f.required;
}}

function showEmpty() {{
  document.getElementById('empty-msg').style.display  = '';
  document.getElementById('props-form').style.display = 'none';
}}

// Sync form → field data → redraw → save (on every input change)
function syncField() {{
  if (sel === null) return;
  const f = fields[sel];
  f.placeholder = document.getElementById('p-ph').value;
  f.font_family = document.getElementById('p-font').value;
  f.font_size   = parseInt(document.getElementById('p-size').value) || 26;
  f.font_color  = document.getElementById('p-color').value;
  f.align       = document.getElementById('p-align').value;
  f.bold        = document.getElementById('p-bold').checked;
  f.required    = document.getElementById('p-req').checked;
  redraw();
  autoSave();
}}

function removeField() {{
  if (sel === null) return;
  fields.splice(sel, 1);
  sel = null;
  showEmpty();
  redraw();
  renderList();
  autoSave();
}}

/* ── Auto-save via bridge ───────────────────── */
function autoSave() {{
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {{
    fetch(BRIDGE, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(fields),
    }}).then(() => {{
      const s = document.getElementById('saved');
      if (s) {{ s.style.display = 'block'; setTimeout(() => s.style.display = 'none', 1200); }}
      // Trigger preview update
      updatePreview();
    }}).catch(() => {{}});
  }}, 250);
}}

function updatePreview() {{
  // Send signal to Streamlit to update preview
  window.parent.postMessage({{
    type: 'streamlit:setComponentValue',
    key: 'preview_update_trigger',
    value: Date.now()
  }}, '*');
}}

function esc(s) {{
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

/* ── Init ───────────────────────────────────── */
renderList();
</script>
</body>
</html>"""


# ─── Build initial layout ─────────────────────────────────────────────────────

def _build_layout(cols: list[str], required_cols: set[str]) -> list[dict]:
    n = len(cols)
    layout = []
    for i, col in enumerate(cols):
        y = 20.0 + (60.0 / max(n - 1, 1)) * i if n > 1 else 50.0
        layout.append({
            "field_key":   col,
            "placeholder": f"{{{col}}}",
            "x_pct":       50.0,
            "y_pct":       round(float(y), 1),
            "font_size":   26,
            "font_family": "Arial",
            "font_color":  "#1a2744",
            "bold":        False,
            "align":       "center",
            "static":      False,
            "required":    col in required_cols,
        })
    return layout


# ─── Main entry point ─────────────────────────────────────────────────────────

def template_editor_ui(
    df_columns: list[str],
    initial_layout: list[dict] | None = None,
    sample_data: dict | None = None,
) -> tuple[Image.Image | None, list[dict]]:
    """
    Design step UI.  Returns (template_image, layout).

    The canvas auto-saves every change via the bridge server.
    Python reads the bridge file on every Streamlit rerun.
    """

    # ── 1. Template source ────────────────────────────────────────────────────
    st.markdown("#### Template")
    
    template: Image.Image | None = None
    up = st.file_uploader("Choose template image", type=["png", "jpg", "jpeg"],
                           label_visibility="visible")
    
    if up:
        template = load_template_from_bytes(up.read())
        st.success(f"✅ Template uploaded: `{up.name}`")
    else:
        st.warning("⚠️ Please upload a template image to continue")
        template = None

    st.divider()

    # Return early if no template uploaded
    if template is None:
        return None, []

    # ── 2. Field selection ────────────────────────────────────────────────────
    st.markdown("#### Choose fields from your dataset")
    st.caption("Tick the columns you want on the certificate. Mark any as Required.")

    if not df_columns:
        st.error("No columns found in the uploaded file.")
        return template, []

    selected_cols: list[str] = []
    required_cols: set[str]  = set()

    h1, h2, h3 = st.columns([1, 4, 2])
    h1.markdown("<small style='color:#52525b'>Include</small>", unsafe_allow_html=True)
    h2.markdown("<small style='color:#52525b'>Column</small>",  unsafe_allow_html=True)
    h3.markdown("<small style='color:#52525b'>Required</small>", unsafe_allow_html=True)

    for col in df_columns:
        c1, c2, c3 = st.columns([1, 4, 2])
        inc = c1.checkbox("", value=False,  key=f"inc_{col}", label_visibility="collapsed")
        c2.markdown(f"`{col}`")
        req = c3.checkbox("", value=False, key=f"req_{col}",
                           disabled=not inc, label_visibility="collapsed")
        if inc:
            selected_cols.append(col)
            if req:
                required_cols.add(col)

    if not selected_cols:
        st.warning("Select at least one column.")
        return template, []

    st.divider()

    # ── 3. Sync session layout with current selection ─────────────────────────
    # Try to pick up the latest layout from the bridge (user may have dragged)
    bridge_layout = _read_bridge()
    bridge_keys   = [f["field_key"] for f in (bridge_layout or [])]
    prev_keys     = [f["field_key"] for f in st.session_state.get("editor_layout", [])]

    if prev_keys != selected_cols:
        # Selection changed — rebuild fresh layout
        new_layout = _build_layout(selected_cols, required_cols)
        st.session_state["editor_layout"] = new_layout
        _write_bridge(new_layout)
    elif bridge_keys == selected_cols and bridge_layout:
        # Bridge has an up-to-date layout — use it (user dragged/edited)
        st.session_state["editor_layout"] = bridge_layout

    layout: list[dict] = st.session_state["editor_layout"]

    # Sync required flags from selection UI
    for f in layout:
        f["required"] = f["field_key"] in required_cols

    # ── 4. Canvas editor ──────────────────────────────────────────────────────
    st.markdown("#### Drag & drop editor")
    st.caption("Drag any field label to reposition it. Click to edit its font, size, colour, and alignment. All changes apply immediately.")

    components.html(
        _canvas_html(_pil_to_b64(template), layout, FONT_NAMES, _BRIDGE_PORT),
        height=520,
        scrolling=False,
    )

    # ── 5. Auto-refresh preview when layout changes ──────────────────────────
    st.divider()
    
    # Check for layout changes and auto-refresh
    current_layout_hash = hash(str(layout))
    if "last_layout_hash" not in st.session_state:
        st.session_state["last_layout_hash"] = current_layout_hash
    elif st.session_state["last_layout_hash"] != current_layout_hash:
        # Layout changed, rerun to update preview
        st.session_state["last_layout_hash"] = current_layout_hash
        st.rerun()
    
    # Manual refresh button as backup
    col_btn, col_info = st.columns([1, 3])
    if col_btn.button("Refresh preview", use_container_width=True):
        # Re-read bridge to pick up latest drag positions
        fresh = _read_bridge()
        if fresh and [f["field_key"] for f in fresh] == selected_cols:
            st.session_state["editor_layout"] = fresh
            layout = fresh
            st.session_state["last_layout_hash"] = hash(str(layout))
            st.rerun()

    req_list = [f["field_key"] for f in layout if f.get("required")]
    if req_list:
        col_info.caption(f"Required: {', '.join(req_list)}")

    st.markdown("#### Preview (with actual data)")
    if sample_data:
        st.caption(f"Previewing with data: {', '.join([f'{k}: {v}' for k, v in list(sample_data.items())[:3]])}")
    else:
        st.caption("Preview with sample text")
    
    prev_img = render_preview(template, layout, sample_data=sample_data)
    buf = io.BytesIO()
    prev_img.save(buf, format="PNG")
    st.image(buf.getvalue(), use_container_width=True)

    return template, layout
