"""Which database objects the orchestrator's Alembic history can see.

The orchestrator and the CRM share one database. The orchestrator owns only tables named
``sdlc_*``, so its history ignores everything else, including the CRM's ``alembic_version``.
"""

TABLE_PREFIX = "sdlc_"
VERSION_TABLE = "sdlc_alembic_version"


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    if type_ == "table":
        return (name or "").startswith(TABLE_PREFIX)
    return True
