from pathlib import Path

from game_automation.common import json_text

ROOT = Path(__file__).resolve().parents[2]


def test_maintained_project_text_is_english_utf8_without_bom_and_lf():
    roots = [
        ROOT / name for name in ("src", "plugins", "skills", "contracts", "config", "docs", "tests")
    ]
    paths = [
        p
        for folder in roots
        for p in folder.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and ".venv" not in p.parts
        and p.suffix in (".py", ".md", ".js", ".css", ".json", ".toml")
    ]
    paths += [
        ROOT / name
        for name in ("README.md", "AGENTS.md", "pyproject.toml", ".editorconfig", ".gitattributes")
    ]
    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"Unexpected BOM: {path}"
        assert b"\r" not in raw, f"Expected LF endings: {path}"
        text = raw.decode("utf-8")
        assert not any(
            0x3400 <= ord(char) <= 0x4DBF or 0x4E00 <= ord(char) <= 0x9FFF for char in text
        ), f"Untranslated source text: {path}"


def test_non_english_user_evidence_remains_lossless(store, run_id):
    value = "\u6e2c\u8a66"
    store.event(run_id, "plugin.evidence", {"original_text": value})
    assert store.events(run_id)[-1]["payload"]["original_text"] == value
    assert value in json_text({"original_text": value})
