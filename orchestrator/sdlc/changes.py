"""Facts about a pull request read from its changed file paths.

Every pull request is read the same way: which module it belongs to, whether it changes the
database schema or governance, how many test files it touches and whether it is docs or config
only. The risk score and the tier floors depend on these facts.
"""

import re
from collections import Counter
from dataclasses import dataclass

DEFAULT_MODULE = "platform"

MIGRATION_FOLDERS = (("alembic", "versions"), ("migrations", "versions"))
GOVERNANCE_PREFIXES = (".github/workflows/", "orchestrator/policies/")
ORCHESTRATOR_PREFIXES = ("orchestrator/", "apps/insights-web/")

TEST_FOLDERS = {"test", "tests", "__tests__"}
TEST_FILE_PATTERN = re.compile(r"^(test_.+\.py|.+\.spec\.(js|ts|jsx|tsx)|.+\.test\..+)$")

DOCS_FOLDERS = ("docs/", ".github/")
DOCS_SUFFIXES = (".md", ".rst", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg")
DOCS_NAMES = {".editorconfig", ".gitattributes", ".gitignore", ".env.example", "license"}
MANIFEST_PATTERN = re.compile(r"^(requirements.*\.txt|package\.json|package-lock\.json)$")


@dataclass(frozen=True)
class FileFacts:
    touches_migration: bool
    touches_governance: bool
    test_files_changed: int
    docs_only: bool
    modules_touched: int


def _normalise(path: str) -> str:
    """Lower-cased, forward-slashed and without a leading ``./`` or ``/``."""
    path = path.strip().replace("\\", "/").lower()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _segments(path: str) -> list[str]:
    return [segment for segment in _normalise(path).split("/") if segment]


def module_of(path: str) -> str | None:
    """The module a path belongs to: the first rule that matches, case-insensitive."""
    normalised = _normalise(path)
    segments = _segments(path)
    if any(segment.startswith("lead") for segment in segments):
        return "leads"
    if any(segment.startswith("account") or "accountdetail" in segment for segment in segments):
        return "accounts"
    if any(segment.startswith(("opportunities", "pipeline")) for segment in segments):
        return "pipeline"
    if "forecast" in normalised:
        return "forecasting"
    if normalised.startswith(ORCHESTRATOR_PREFIXES):
        return "orchestrator"
    return None


def infer_module(paths: list[str]) -> str:
    """The module with the most matching paths; ``platform`` when none match."""
    counts = Counter(module for module in map(module_of, paths) if module is not None)
    if not counts:
        return DEFAULT_MODULE
    return counts.most_common(1)[0][0]


def is_test_file(path: str) -> bool:
    segments = _segments(path)
    if not segments:
        return False
    if any(segment in TEST_FOLDERS for segment in segments[:-1]):
        return True
    return bool(TEST_FILE_PATTERN.match(segments[-1]))


def _touches_governance(path: str) -> bool:
    return _normalise(path).startswith(GOVERNANCE_PREFIXES)


def _touches_migration(path: str) -> bool:
    folders = _segments(path)[:-1]
    return any(pair in MIGRATION_FOLDERS for pair in zip(folders, folders[1:], strict=False))


def is_docs_or_config(path: str) -> bool:
    """Docs or config that changes nothing that runs.

    Dependency manifests, CI workflows and policies are never docs: they change what runs, or
    governance itself.
    """
    normalised = _normalise(path)
    segments = _segments(path)
    if not segments:
        return False
    name = segments[-1]
    if MANIFEST_PATTERN.match(name) or _touches_governance(path):
        return False
    return normalised.startswith(DOCS_FOLDERS) or name.endswith(DOCS_SUFFIXES) or name in DOCS_NAMES


def classify_files(paths: list[str]) -> FileFacts:
    modules = {module for module in map(module_of, paths) if module is not None}
    return FileFacts(
        touches_migration=any(_touches_migration(path) for path in paths),
        touches_governance=any(_touches_governance(path) for path in paths),
        test_files_changed=sum(1 for path in paths if is_test_file(path)),
        docs_only=bool(paths) and all(is_docs_or_config(path) for path in paths),
        modules_touched=max(1, len(modules)),
    )
