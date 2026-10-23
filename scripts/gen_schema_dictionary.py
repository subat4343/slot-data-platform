"""docs/schema_dictionary.md を ORM メタデータから生成する（実装と定義書のずれを防ぐ）。

使い方: python scripts/gen_schema_dictionary.py > docs/schema_dictionary.md
"""

from __future__ import annotations

import sys

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.schema import AddConstraint

from slot_data.models import Base

DIALECT = postgresql.dialect()


def column_type(col) -> str:
    return col.type.compile(dialect=DIALECT)


def main() -> None:
    out = sys.stdout
    out.write("# テーブル定義書\n\n")
    out.write("> `scripts/gen_schema_dictionary.py` が ORM メタデータから生成。手で編集しない。\n")
    out.write(
        "> トリガー（追記専用・配置版不変・ID 対応の参照整合）は `alembic/versions/0001_initial_schema.py` を参照。\n\n"
    )
    for table in sorted(Base.metadata.sorted_tables, key=lambda t: t.name):
        out.write(f"## {table.name}\n\n")
        cls = next((m.class_ for m in Base.registry.mappers if m.local_table is table), None)
        if cls is not None and cls.__doc__:
            out.write(" ".join(line.strip() for line in cls.__doc__.strip().splitlines()) + "\n\n")
        out.write("| 列 | 型 | NULL | 既定値 | 参照 |\n|---|---|---|---|---|\n")
        for col in table.columns:
            fks = ", ".join(f"{fk.column.table.name}.{fk.column.name}" for fk in col.foreign_keys)
            default = ""
            if col.server_default is not None:
                default = str(
                    col.server_default.arg.text
                    if hasattr(col.server_default.arg, "text")
                    else col.server_default.arg
                )
            elif col.default is not None:
                default = "アプリ側生成（UUIDv7）" if col.primary_key else "アプリ側"
            pk = " (PK)" if col.primary_key else ""
            out.write(
                f"| `{col.name}`{pk} | {column_type(col)} | {'可' if col.nullable else '不可'} | {default} | {fks} |\n"
            )
        constraints = []
        for c in sorted(table.constraints, key=lambda c: str(c.name)):
            if isinstance(c, UniqueConstraint):
                constraints.append(f"- UNIQUE `{c.name}`: ({', '.join(col.name for col in c.columns)})")
            elif isinstance(c, ExcludeConstraint):
                ddl = str(AddConstraint(c).compile(dialect=DIALECT))
                constraints.append(f"- `{c.name}`: `{' '.join(ddl[ddl.index('EXCLUDE') :].split())}`")
            elif isinstance(c, CheckConstraint):
                constraints.append(f"- CHECK `{c.name}`: `{c.sqltext}`")
            elif isinstance(c, ForeignKeyConstraint) and len(c.columns) > 1:
                constraints.append(
                    f"- 複合 FK `{c.name}`: ({', '.join(col.name for col in c.columns)}) → "
                    f"{c.referred_table.name}({', '.join(e.column.name for e in c.elements)})"
                )
        for idx in sorted(table.indexes, key=lambda i: str(i.name)):
            assert isinstance(idx, Index)
            exprs = ", ".join(str(e) if not hasattr(e, "name") else e.name for e in idx.expressions)
            where = idx.dialect_options["postgresql"].get("where")
            kind = "UNIQUE INDEX" if idx.unique else "INDEX"
            constraints.append(
                f"- {kind} `{idx.name}`: ({exprs}){f' WHERE {where}' if where is not None else ''}"
            )
        if constraints:
            out.write("\n**制約・索引**\n\n" + "\n".join(constraints) + "\n")
        out.write("\n")


if __name__ == "__main__":
    main()
