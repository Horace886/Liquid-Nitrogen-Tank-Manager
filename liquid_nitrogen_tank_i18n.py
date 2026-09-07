"""UI-only messages. Canonical strings and all persisted business data stay unchanged."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
import weakref

ENGLISH = json.loads(Path(__file__).with_name("ui_en.json").read_text(encoding="utf-8"))


class Message(str):
    """A Chinese source string with a deferred English rendering.

    Subclassing str keeps existing filter comparisons, JSON/SQLite writes and
    Excel output unchanged. Only UI rendering explicitly calls ``render``.
    Dynamic arguments remain separate, so user-entered names are never looked
    up in the translation catalog.
    """

    def __new__(cls, source: str, *values: object):
        canonical = source.format(*values) if values else source
        obj = super().__new__(cls, canonical)
        obj.source = source
        obj.values = values
        return obj

    def __add__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return Message("{0}{1}", self, other)

    def __radd__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return Message("{0}{1}", other, self)

    def __reduce__(self):
        return (Message, (self.source, *self.values))


msg = Message


def join_text(separator: str, values) -> Message:
    values = tuple(values)
    source = separator.replace("{", "{{").replace("}", "}}").join(
        "{" + str(index) + "}" for index in range(len(values))
    )
    return Message(source, *values)


def render(value: object, language: str = "zh") -> str:
    if not isinstance(value, Message):
        return str(value)
    template = ENGLISH.get(value.source, value.source) if language == "en" else value.source
    if not value.values:
        return template
    return template.format(*(render(item, language) for item in value.values))


def translated(value: object) -> bool:
    return isinstance(value, Message) and (
        value.source in ENGLISH or any(translated(item) for item in value.values)
    )


# Only used for application-generated errors, never for sample/person data.
# Match the known Chinese template and keep interpolated values as raw text.
_error_patterns = []
for _source in ENGLISH:
    _parts = re.split(r'(\{\d+\})', _source)
    if len(_parts) > 1 and any('\u4e00' <= char <= '\u9fff' for char in _source):
        _indices = [int(part[1:-1]) for part in _parts if re.fullmatch(r'\{\d+\}', part)]
        if _indices == list(range(len(_indices))):
            _pattern = ''.join('(.*?)' if re.fullmatch(r'\{\d+\}', part) else re.escape(part) for part in _parts)
            _error_patterns.append((re.compile(_pattern, re.DOTALL), _source))


def error_message(value: object) -> Message:
    text = str(value)
    if text in ENGLISH:
        return msg(text)
    for pattern, source in _error_patterns:
        match = pattern.fullmatch(text)
        if match:
            return msg(source, *match.groups())
    return msg('{0}', text)


def preference_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config"))
    return base / "LiquidNitrogenTankManager" / "preferences.json"


class LanguageState:
    def __init__(self, path: Path):
        self.path = path
        self.settings = {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                self.settings = value
        except (OSError, ValueError):
            pass
        # Preferences written before the bilingual release did not represent
        # an intentional user choice. Start those installations in Chinese
        # once; subsequent selections carry the explicit marker below.
        self.language = self.settings.get("language", "zh") if self.settings.get("language_selected") is True else "zh"
        if self.language not in {"zh", "en"}:
            self.language = "zh"
        self.widgets = weakref.WeakSet()

    def set_language(self, language: str, *, persist: bool = True) -> None:
        if language not in {"zh", "en"}:
            raise ValueError("Unsupported language")
        if persist:
            settings = {**self.settings, "language": language, "language_selected": True}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix="preferences-", suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(settings, output, ensure_ascii=False, indent=2)
                os.replace(temporary, self.path)
                self.settings = settings
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        self.language = language
        for widget in list(self.widgets):
            if widget.winfo_exists():
                widget._apply_language()
