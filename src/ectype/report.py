"""Small helpers for the static HTML reports every stage writes to reports/."""

from __future__ import annotations

import html
import json
from pathlib import Path

_STYLE = """
:root { color-scheme: light dark; --fg: #1d1d1f; --bg: #fff; --muted: #6e6e73; --line: #d2d2d7; }
@media (prefers-color-scheme: dark) { :root { --fg: #f5f5f7; --bg: #1c1c1e; --muted: #a1a1a6; --line: #3a3a3c; } }
body { font: 14px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); margin: 24px; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 28px; } h3 { font-size: 15px; }
table { border-collapse: collapse; margin: 8px 0 16px; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
th { font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.row figure { margin: 0; flex: 1 1 300px; max-width: 100%; }
.row img, .sheet { max-width: 100%; border: 1px solid var(--line); background: #fff; }
figcaption { color: var(--muted); font-size: 12px; }
code { font-size: 13px; }
"""


def esc(value: object) -> str:
    return html.escape(str(value))


def table(headers: list[str], rows: list[list[object]], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in numeric else ""
            cells.append(f"<td{cls}>{esc(cell)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def write_page(path: Path, title: str, body: str) -> None:
    path.write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{esc(title)}</title><style>{_STYLE}</style></head>"
        f"<body><h1>{esc(title)}</h1>{body}</body></html>",
        encoding="utf-8",
    )


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
