import re
from pathlib import Path

import pytest

from app.main import TAGS, app

README = Path(__file__).resolve().parents[1] / "README.md"
TITLE = "PragMattie Sync CRM API"
# Schemas FastAPI adds itself for 422 bodies; they are not ours to describe.
FASTAPI_SCHEMAS = {"HTTPValidationError", "ValidationError"}
TAG_NAMES = {tag["name"] for tag in TAGS}


def _operations():
    for path, methods in app.openapi()["paths"].items():
        for method, operation in methods.items():
            yield method.upper(), path, operation


def _readme_endpoints():
    """(method, path) for each row of the README's endpoint table."""
    return re.findall(
        r"^\| `(GET|POST|PATCH|PUT|DELETE)` \| `([^`]+)` \|", README.read_text(), re.M
    )


@pytest.mark.parametrize("page", ["/docs", "/redoc"])
def test_docs_pages_load_with_the_api_title(client, page):
    response = client.get(page)

    assert response.status_code == 200
    assert TITLE in response.text


def test_openapi_document_is_titled_for_the_crm(client):
    document = client.get("/openapi.json").json()

    assert document["info"]["title"] == TITLE


def test_every_endpoint_has_a_summary_and_one_known_tag():
    operations = list(_operations())

    assert operations
    for method, path, operation in operations:
        assert operation.get("summary"), f"{method} {path} has no summary"
        assert len(operation.get("tags", [])) == 1, f"{method} {path} needs exactly one tag"
        assert operation["tags"][0] in TAG_NAMES, f"{method} {path} has an unknown tag"


def test_every_parameter_has_a_description():
    for method, path, operation in _operations():
        for parameter in operation.get("parameters", []):
            assert parameter.get("description"), f"{method} {path}: {parameter['name']}"


def test_every_schema_field_has_a_description():
    schemas = app.openapi()["components"]["schemas"]

    for name, schema in schemas.items():
        if name in FASTAPI_SCHEMAS:
            continue
        for field, spec in schema.get("properties", {}).items():
            assert spec.get("description"), f"{name}.{field} has no description"


def test_readme_endpoint_table_matches_the_openapi_document():
    documented = _readme_endpoints()
    served = [(method, path) for method, path, _ in _operations()]

    assert len(documented) == len(set(documented)), "the README lists an endpoint twice"
    assert set(documented) == set(served)
