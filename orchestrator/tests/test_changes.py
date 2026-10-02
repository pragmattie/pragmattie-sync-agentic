import dataclasses

import pytest

from sdlc.changes import (
    FileFacts,
    classify_files,
    infer_module,
    is_docs_or_config,
    is_test_file,
    module_of,
)


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("apps/crm-web/src/views/LeadsView.vue", "leads"),
        ("apps/api/app/routers/leads.py", "leads"),
        ("apps/crm-web/src/components/NewLeadDialog.vue", None),
        ("apps/api/migrations/versions/0003_leads.py", None),
        ("apps/api/tests/test_leads.py", None),
        ("apps/api/app/routers/accounts.py", "accounts"),
        ("apps/crm-web/src/views/AccountsView.vue", "accounts"),
        ("apps/crm-web/src/views/AccountDetailView.vue", "accounts"),
        ("apps/crm-web/src/components/MyAccountDetailCard.vue", "accounts"),
        ("apps/api/app/routers/opportunities.py", "pipeline"),
        ("apps/crm-web/src/views/PipelineView.vue", "pipeline"),
        ("apps/api/app/forecast.py", "forecasting"),
        ("apps/crm-web/src/views/ForecastView.vue", "forecasting"),
        ("apps/crm-web/src/charts/forecast.js", "forecasting"),
        ("orchestrator/sdlc/main.py", "orchestrator"),
        ("apps/insights-web/src/App.vue", "orchestrator"),
        ("APPS/API/APP/ROUTERS/LEADS.PY", "leads"),
        ("apps/api/app/main.py", None),
        ("README.md", None),
        ("apps/crm-web/src/views/HomeView.vue", None),
    ],
)
def test_module_of(path, module):
    assert module_of(path) == module


def test_module_of_takes_the_first_rule_that_matches():
    assert module_of("apps/crm-web/src/views/LeadsForecastView.vue") == "leads"
    assert module_of("orchestrator/sdlc/pipeline_events.py") == "pipeline"


@pytest.mark.parametrize(
    ("paths", "module"),
    [
        ([], "platform"),
        (["README.md", "apps/api/app/main.py"], "platform"),
        (["apps/api/app/routers/leads.py"], "leads"),
        (
            [
                "apps/api/app/routers/opportunities.py",
                "apps/crm-web/src/views/PipelineView.vue",
                "apps/api/app/routers/leads.py",
                "README.md",
            ],
            "pipeline",
        ),
    ],
)
def test_infer_module(paths, module):
    assert infer_module(paths) == module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("apps/api/tests/test_leads.py", True),
        ("apps/api/tests/conftest.py", True),
        ("apps/crm-web/src/tests/setup.js", True),
        ("apps/crm-web/src/tests/LeadsView.spec.js", True),
        ("apps/crm-web/src/__tests__/Thing.vue", True),
        ("apps/crm-web/test/thing.js", True),
        ("scripts/test_tool.py", True),
        ("src/widget.spec.ts", True),
        ("src/widget.spec.tsx", True),
        ("src/widget.spec.jsx", True),
        ("src/widget.test.js", True),
        ("src/widget.test.py", True),
        ("apps/api/app/routers/leads.py", False),
        ("apps/api/app/testing.py", False),
        ("src/widget.spec.vue", False),
        ("apps/crm-web/src/views/LeadsView.vue", False),
        ("tests", False),
    ],
)
def test_is_test_file(path, expected):
    assert is_test_file(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", True),
        ("docs/architecture.png", True),
        (".github/CODEOWNERS", True),
        (".github/ISSUE_TEMPLATE/feature.yml", True),
        ("ci/proposed/ci.yml", True),
        ("orchestrator/pyproject.toml", True),
        ("orchestrator/alembic.ini", True),
        ("setup.cfg", True),
        ("notes.rst", True),
        ("docker-compose.yaml", True),
        (".editorconfig", True),
        (".gitattributes", True),
        (".gitignore", True),
        (".env.example", True),
        ("LICENSE", True),
        ("orchestrator/requirements.txt", False),
        ("orchestrator/requirements-dev.txt", False),
        ("apps/crm-web/package.json", False),
        ("apps/crm-web/package-lock.json", False),
        (".github/workflows/ci.yml", False),
        ("orchestrator/policies/tiers.yml", False),
        ("apps/api/app/main.py", False),
        ("apps/crm-web/src/App.vue", False),
    ],
)
def test_is_docs_or_config(path, expected):
    assert is_docs_or_config(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("apps/api/migrations/versions/0002_x.py", True),
        ("orchestrator/migrations/versions/0001_sdlc.py", True),
        ("some/service/alembic/versions/abc123_init.py", True),
        ("apps/api/app/migrations_filter.py", False),
        ("apps/api/migrations/env.py", False),
        ("orchestrator/tests/test_migrations.py", False),
    ],
)
def test_touches_migration(path, expected):
    assert classify_files([path]).touches_migration is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".github/workflows/ci.yml", True),
        ("orchestrator/policies/tiers.yml", True),
        ("ci/proposed/ci.yml", False),
        (".github/CODEOWNERS", False),
        ("orchestrator/sdlc/main.py", False),
    ],
)
def test_touches_governance(path, expected):
    assert classify_files([path]).touches_governance is expected


def test_ci_workflow_touches_governance_and_is_not_docs_only():
    facts = classify_files([".github/workflows/ci.yml"])

    assert facts.touches_governance is True
    assert facts.docs_only is False


def test_proposed_ci_is_neither_governance_nor_code():
    facts = classify_files(["ci/proposed/ci.yml"])

    assert facts.touches_governance is False
    assert facts.docs_only is True


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["README.md"], True),
        (["README.md", "docs/plan.md", "orchestrator/pyproject.toml"], True),
        (["apps/crm-web/package.json"], False),
        (["orchestrator/requirements.txt"], False),
        (["README.md", "apps/api/app/main.py"], False),
        ([], False),
    ],
)
def test_docs_only(paths, expected):
    assert classify_files(paths).docs_only is expected


def test_counts_test_files():
    facts = classify_files(
        [
            "apps/api/app/routers/leads.py",
            "apps/api/tests/test_leads.py",
            "apps/api/tests/test_leads_convert.py",
            "apps/crm-web/src/tests/LeadsView.spec.js",
        ]
    )

    assert facts.test_files_changed == 3


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], 1),
        (["README.md"], 1),
        (["apps/api/app/routers/leads.py", "apps/crm-web/src/views/LeadsView.vue"], 1),
        (["apps/api/app/routers/leads.py", "apps/crm-web/src/views/PipelineView.vue"], 2),
        (
            [
                "apps/api/app/routers/leads.py",
                "apps/api/app/routers/accounts.py",
                "apps/api/app/forecast.py",
                "apps/insights-web/src/App.vue",
            ],
            4,
        ),
    ],
)
def test_modules_touched(paths, expected):
    assert classify_files(paths).modules_touched == expected


def test_classify_files_returns_all_facts():
    facts = classify_files(
        [
            "apps/api/migrations/versions/0005_activities.py",
            "apps/api/app/routers/leads.py",
            "apps/api/tests/test_leads.py",
        ]
    )

    assert facts == FileFacts(
        touches_migration=True,
        touches_governance=False,
        test_files_changed=1,
        docs_only=False,
        modules_touched=1,
    )


def test_file_facts_are_frozen():
    facts = classify_files(["README.md"])

    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.docs_only = False
