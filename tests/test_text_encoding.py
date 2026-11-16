"""Text files are read and written as UTF-8, said outright. Left to itself, Python on Windows
uses the old ANSI code page, so a dash or a Hebrew letter comes back as "?" or stops the run."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ["src/ectype", "tools"]
# Places left as they are on purpose: (file, function called). Each needs a reason.
ALLOWED = {
    ("src/ectype/converter.py", "Popen"),  # Ectype IDML's own output: its encoding is decided in T-013
}


def _mode(call):
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return str(k.value.value)
    return "r"


def offenders(path):
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        given = {k.arg for k in node.keywords}
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if "encoding" in given:
            continue
        if name in ("read_text", "write_text"):
            # a file's own read_text / write_text, not Ectype's functions of the same name
            ours = isinstance(f, ast.Attribute) and ast.unparse(f.value).endswith("project")  # Project.write_text
            if isinstance(f, ast.Attribute) and not ours and given <= {"errors", "newline"} and len(node.args) == (name == "write_text"):
                found.append((node.lineno, name))
        elif name == "open" and isinstance(f, ast.Name) and "b" not in _mode(node):
            found.append((node.lineno, name))
        elif name in ("run", "Popen", "check_output") and given & {"text", "universal_newlines"}:
            found.append((node.lineno, name))
    return found


def test_text_is_read_and_written_as_utf8():
    bad = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            bad += [f"{rel}:{line} {name}" for line, name in offenders(path) if (rel, name) not in ALLOWED]
    assert not bad, "no encoding given:\n" + "\n".join(bad)
