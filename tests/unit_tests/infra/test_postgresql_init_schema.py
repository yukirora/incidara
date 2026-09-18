"""Fresh-install schema guards for infra/postgresql/init.sql.

The Postgres entrypoint runs init scripts with ON_ERROR_STOP=1, so a single bad
statement aborts the rest of the file and leaves a new deployment with a partial
schema while the container still reports healthy. A foreign key whose target
column is not unique is the failure mode this file guards against.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INIT_SQL = (ROOT / "infra" / "postgresql" / "init.sql").read_text()

CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(?P<name>\w+)\s*\((?P<body>.*?)\n\);",
    re.DOTALL,
)
FOREIGN_KEY = re.compile(r"REFERENCES\s+(?P<table>\w+)\s*\(\s*(?P<column>\w+)\s*\)")


def _tables() -> dict[str, str]:
    tables = {match["name"]: match["body"] for match in CREATE_TABLE.finditer(INIT_SQL)}
    assert tables, "init.sql declares no tables — parsing or file layout changed"
    return tables


def _unique_columns(table: str, body: str) -> set[str]:
    """Columns that can be a foreign key target: single-column PK or UNIQUE."""
    unique: set[str] = set()
    for line in body.splitlines():
        line = line.split("--")[0].strip().rstrip(",")
        if not line or line.startswith(("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK")):
            continue
        parts = line.split()
        if len(parts) >= 2 and {"PRIMARY", "UNIQUE"} & set(parts[1:]):
            unique.add(parts[0])
    for match in re.finditer(
        rf"CREATE UNIQUE INDEX IF NOT EXISTS \w+\s+ON\s+{table}\(\s*(\w+)\s*\)", INIT_SQL
    ):
        unique.add(match.group(1))
    return unique


def test_every_foreign_key_targets_a_unique_column():
    tables = _tables()
    missing = []
    for table, body in tables.items():
        for fk in FOREIGN_KEY.finditer(body):
            target, column = fk["table"], fk["column"]
            if target not in tables:
                missing.append(f"{table} references unknown table {target}")
            elif column not in _unique_columns(target, tables[target]):
                missing.append(f"{table} references {target}({column}) which is not unique")
    assert not missing, "foreign keys that fail on a fresh database: " + "; ".join(missing)


def test_rejected_proposals_references_analysis_problems():
    """Regression guard: this table silently vanished from fresh installs."""
    tables = _tables()
    assert "rejected_proposals" in tables
    assert FOREIGN_KEY.search(tables["rejected_proposals"])["table"] == "analysis_problems"
    assert "problem_id" in _unique_columns("analysis_problems", tables["analysis_problems"])
