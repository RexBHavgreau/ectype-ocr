"""Review and correct OCR in the browser; every correction is kept as training data.

`ectype review <workspace>` serves a small local site (127.0.0.1 only). Each page
shows the scan with every word boxed: low-confidence words in red, words holding
a glyph that looks like another character (from the cluster report) in orange.
Click a word, fix its text, save. The correction updates <workspace>/ocr, is logged
in <workspace>/corrections/<page>.json, and the corrected line is written as a
training pair, <workspace>/training/<page>-l<line>.png + .gt.txt (the line image
and its text, as Kraken's ketos and most OCR trainers take them). Then
`ectype rebuild <workspace>` reruns segmentation through the output PDF.
"""

from __future__ import annotations

import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from . import report
from .ocr import Char, OcrPage


def apply_correction(ocr: OcrPage, line: int, word: int, text: str) -> str:
    """Set a word's text. Character boxes are kept when the length is unchanged, else
    spread evenly over the word box. A corrected word counts as fully confident."""
    w = ocr.lines[line].words[word]
    old = w.text
    text = text.strip()
    if not text:
        raise ValueError("empty correction; delete words isn't supported")
    if len(text) == len(w.chars):
        for c, t in zip(w.chars, text):
            c.text, c.conf = t, 100.0
    else:
        x0, y0, x1, y1 = w.box
        step = (x1 - x0) / len(text)
        w.chars = [Char(t, [round(x0 + k * step), y0, round(x0 + (k + 1) * step), y1], 100.0) for k, t in enumerate(text)]
    w.text, w.conf = text, 100.0
    return old


def save_training_line(workspace: Path, page: str, ocr: OcrPage, line: int) -> Path:
    """Crop a line from the page image and write it with its text as a training pair."""
    ln = ocr.lines[line]
    x0, y0, x1, y1 = ln.box
    pad_y = max(4, (y1 - y0) // 4)
    image = Image.open(Path(workspace) / "pages" / f"{page}.png")
    crop = image.crop((max(0, x0 - 10), max(0, y0 - pad_y), min(image.width, x1 + 10), min(image.height, y1 + pad_y)))
    out = Path(workspace) / "training"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{page}-l{line:03d}"
    crop.save(stem.with_suffix(".png"))
    stem.with_suffix(".gt.txt").write_text(" ".join(w.text for w in ln.words) + "\n", encoding="utf-8", newline="\n")
    return stem.with_suffix(".png")


def save_training_word(workspace: Path, page: str, ocr: OcrPage, line: int, word: int) -> Path | None:
    """A word in another script than the main text's (Hebrew, Greek...), cropped with its text
    as a training pair in training/words/: those scripts' models are trained on words, since
    their lines mix scripts."""
    from .scripts import UNICODE_NAME, in_script

    w = ocr.lines[line].words[word]
    if not any(in_script(w.text, script) for script in UNICODE_NAME if script != "Latin"):
        return None
    x0, y0, x1, y1 = w.box
    pad = max(6, (y1 - y0) // 3)
    image = Image.open(Path(workspace) / "pages" / f"{page}.png")
    crop = image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))
    out = Path(workspace) / "training" / "words"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{page}-l{line:03d}-w{word:03d}"
    crop.save(stem.with_suffix(".png"))
    stem.with_suffix(".gt.txt").write_text(w.text + "\n", encoding="utf-8", newline="\n")
    return stem.with_suffix(".png")


def correct(workspace: Path, page: str, line: int, word: int, text: str) -> dict:
    """Apply one correction to a workspace: OCR file, correction log, training pair."""
    workspace = Path(workspace)
    path = workspace / "ocr" / f"{page}.ocr.json"
    original = workspace / "ocr" / f"{page}.ocr.original.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not original.is_file():
        original.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    ocr = OcrPage.from_json(data)
    old = apply_correction(ocr, line, word, text)
    report.write_json(path, ocr.to_json())
    log_path = workspace / "corrections" / f"{page}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else []
    entry = {"line": line, "word": word, "old": old, "new": text.strip(), "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    log.append(entry)
    report.write_json(log_path, log)
    pair = save_training_line(workspace, page, ocr, line)
    save_training_word(workspace, page, ocr, line, word)
    return {**entry, "training": str(pair.relative_to(workspace))}


def carry_corrections(workspace: Path, page_name: str, page: OcrPage) -> int:
    """Carry the page's corrections over to a new reading of it (other languages, a trained
    model): each corrected word's text goes to the new word that covers the same place on the
    page. The new reading becomes the page's original, and the log points at the new words.
    Returns how many corrections were carried over."""
    import copy

    workspace = Path(workspace)
    log_path = workspace / "corrections" / f"{page_name}.json"
    original = workspace / "ocr" / f"{page_name}.ocr.original.json"
    if not log_path.is_file() or not original.is_file():
        return 0
    old = OcrPage.from_json(json.loads(original.read_text(encoding="utf-8")))
    raw = copy.deepcopy(page)
    log = json.loads(log_path.read_text(encoding="utf-8"))
    words = [(li, wi, w.box) for li, line in enumerate(page.lines) for wi, w in enumerate(line.words)]

    def overlap(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    carried = 0
    for e in log:
        try:
            box = old.lines[e["line"]].words[e["word"]].box
        except IndexError:
            e["lost"] = True
            continue
        best = max(words, key=lambda t: overlap(box, t[2]), default=None)
        if best is None or overlap(box, best[2]) < 0.3:
            e["lost"] = True
            continue
        apply_correction(page, best[0], best[1], e["new"])
        e["line"], e["word"] = best[0], best[1]
        e.pop("lost", None)
        carried += 1
    original.write_text(json.dumps(raw.to_json(), ensure_ascii=False), encoding="utf-8", newline="\n")
    report.write_json(log_path, log)
    return carried


def _flags(workspace: Path) -> dict[str, set[tuple[int, int]]]:
    """Words holding a probable misreading, per page, from the cluster report."""
    path = workspace / "cluster" / "clusters.json"
    out: dict[str, set[tuple[int, int]]] = {}
    if not path.is_file():
        return out
    suspects = json.loads(path.read_text(encoding="utf-8")).get("suspects", [])
    units: dict[str, set[int]] = {}
    for s in suspects:
        for page, unit in s["instances"]:
            units.setdefault(page, set()).add(unit)
    for page, ids in units.items():
        seg_path = workspace / "segment" / f"{page}.seg.json"
        if seg_path.is_file():
            seg = json.loads(seg_path.read_text(encoding="utf-8"))
            out[page] = {(u["line"], u["word"]) for u in seg["units"] if u["id"] in ids}
    return out


_PAGE_CSS = """
.page { position: relative; max-width: 100%; }
.page img { width: 100%; display: block; }
.w { position: absolute; border: 1px solid rgba(40,90,220,.25); cursor: pointer; }
.w:hover { background: rgba(40,90,220,.15); }
.w.low { border: 2px solid #dc2828; }
.w.sus { border: 2px solid #e8890c; }
.w.fixed { border: 2px solid #1a9850; background: rgba(26,152,80,.12); }
#editor { position: sticky; top: 0; background: var(--bg); padding: 8px 0; z-index: 2; border-bottom: 1px solid var(--line); }
#editor input { font: 16px system-ui; padding: 4px 6px; width: 18em; }
.legend span { margin-right: 14px; }
"""


def _page_html(workspace: Path, page: str, flags: set[tuple[int, int]], low_conf: float) -> str:
    ocr = OcrPage.from_json(json.loads((workspace / "ocr" / f"{page}.ocr.json").read_text(encoding="utf-8")))
    width, height = ocr.size_px
    fixed = set()
    log = workspace / "corrections" / f"{page}.json"
    if log.is_file():
        fixed = {(e["line"], e["word"]) for e in json.loads(log.read_text(encoding="utf-8"))}
    boxes = []
    for li, line in enumerate(ocr.lines):
        for wi, w in enumerate(line.words):
            x0, y0, x1, y1 = w.box
            cls = "w" + (" fixed" if (li, wi) in fixed else " sus" if (li, wi) in flags else " low" if w.conf < low_conf else "")
            style = (f"left:{100 * x0 / width:.3f}%;top:{100 * y0 / height:.3f}%;"
                     f"width:{100 * (x1 - x0) / width:.3f}%;height:{100 * (y1 - y0) / height:.3f}%")
            boxes.append(f'<div class="{cls}" style="{style}" data-l="{li}" data-w="{wi}" '
                         f'title="{html.escape(w.text)} ({w.conf:.0f})" data-t="{html.escape(w.text)}"></div>')
    script = """
let cur = null;
const ed = document.getElementById('t'), info = document.getElementById('info');
document.querySelectorAll('.w').forEach(b => b.addEventListener('click', () => {
  cur = b; ed.value = b.dataset.t; info.textContent = 'line ' + b.dataset.l + ', word ' + b.dataset.w; ed.focus(); ed.select();
}));
async function save() {
  if (!cur) return;
  const r = await fetch('/api/correct', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({page: PAGE, line: +cur.dataset.l, word: +cur.dataset.w, text: ed.value})});
  const j = await r.json();
  if (j.error) { info.textContent = 'Not saved: ' + j.error; return; }
  cur.dataset.t = j.new; cur.title = j.new; cur.className = 'w fixed';
  info.textContent = j.old === j.new ? 'Confirmed: ' + j.new : 'Saved: ' + j.old + ' → ' + j.new;
}
ed.addEventListener('keydown', e => { if (e.key === 'Enter') save(); });
document.getElementById('s').addEventListener('click', save);
"""
    body = (
        f'<p><a href="/">All pages</a></p>'
        f'<div id="editor"><input id="t" placeholder="Click a word"> <button id="s">Save</button> '
        f'<span id="info" class="muted"></span>'
        f'<div class="legend muted"><span style="color:#dc2828">red: low OCR confidence</span>'
        f'<span style="color:#e8890c">orange: glyph looks like another character</span>'
        f'<span style="color:#1a9850">green: corrected or confirmed</span></div></div>'
        f'<div class="page"><img src="/img/{html.escape(page)}.png" alt="page {html.escape(page)}">{"".join(boxes)}</div>'
        f"<script>const PAGE = {json.dumps(page)};{script}</script>"
    )
    return body


def _index_html(workspace: Path, pages: list[str], flags: dict, low_conf: float) -> str:
    rows = []
    for page in pages:
        path = workspace / "ocr" / f"{page}.ocr.json"
        ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
        words = list(ocr.words())
        log = workspace / "corrections" / f"{page}.json"
        fixed = len(json.loads(log.read_text(encoding="utf-8"))) if log.is_file() else 0
        rows.append([page, len(words), sum(w.conf < low_conf for w in words), len(flags.get(page, ())), fixed])
    table = report.table(["Page", "Words", "Low confidence", "Probable misreadings", "Corrections"], rows, numeric={1, 2, 3, 4})
    links = " ".join(f'<a href="/page/{html.escape(p)}">{html.escape(p)}</a>' for p in pages)
    return (f"<p class=\"muted\">Click a page, then any word to correct it. Corrections update the OCR, are logged, "
            f"and save the corrected line as training data in <code>training/</code>. When done, run "
            f"<code>ectype rebuild {html.escape(str(workspace))}</code>.</p><p>{links}</p>{table}")


def _wrap(title: str, body: str, extra_css: str = "") -> bytes:
    return report.render_page(title, body, extra_css).encode("utf-8")


def make_handler(workspace: Path, low_conf: float = 70.0):
    workspace = Path(workspace)

    class Handler(BaseHTTPRequestHandler):
        def _pages(self) -> list[str]:
            return sorted(p.stem.removesuffix(".ocr") for p in (workspace / "ocr").glob("*.ocr.json"))

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # quiet
            pass

        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            pages = self._pages()
            if path == "/":
                self._send(200, _wrap("Review", _index_html(workspace, pages, _flags(workspace), low_conf)), "text/html; charset=utf-8")
            elif path.startswith("/page/") and path[6:] in pages:
                page = path[6:]
                body = _page_html(workspace, page, _flags(workspace).get(page, set()), low_conf)
                self._send(200, _wrap(f"Review {page}", body, _PAGE_CSS), "text/html; charset=utf-8")
            elif path.startswith("/img/") and path[5:].removesuffix(".png") in pages:
                self._send(200, (workspace / "pages" / path[5:]).read_bytes(), "image/png")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/correct":
                self._send(404, b"not found", "text/plain")
                return
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if data["page"] not in self._pages():
                    raise ValueError("unknown page")
                result = correct(workspace, data["page"], int(data["line"]), int(data["word"]), str(data["text"]))
                self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")

    return Handler


def serve(workspace: Path, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(workspace))
