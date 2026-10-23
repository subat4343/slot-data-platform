"""パーサー登録簿。原本には解析に使うパーサー名を記録し、再解析時はその名前の現行版で解析する。"""

from __future__ import annotations

from slot_data.adapters.base import Parser
from slot_data.adapters.manual import ManualCsvParser, ManualJsonParser

_PARSERS: dict[str, Parser] = {}


def register_parser(parser: Parser) -> None:
    _PARSERS[parser.name] = parser


def get_parser(name: str) -> Parser:
    try:
        return _PARSERS[name]
    except KeyError as exc:
        raise KeyError(f"parser not registered: {name}") from exc


register_parser(ManualJsonParser())
register_parser(ManualCsvParser())
