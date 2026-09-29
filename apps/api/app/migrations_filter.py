"""Which database objects the CRM's Alembic history can see.

The CRM and the orchestrator share one database. Tables named ``sdlc_*`` (including the
orchestrator's ``sdlc_alembic_version``) belong to the orchestrator, so the CRM's history ignores
them.
"""

ORCHESTRATOR_TABLE_PREFIX = "sdlc_"


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    if type_ == "table":
        return not (name or "").startswith(ORCHESTRATOR_TABLE_PREFIX)
    return True
