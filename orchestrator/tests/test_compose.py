import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
PORT_VARIABLES = (
    "CRM_WEB_PORT",
    "INSIGHTS_WEB_PORT",
    "API_PORT",
    "ORCH_PORT",
    "MYSQL_HOST_PORT",
    "COLLECT_SECONDS",
)
BESIDE_V1 = {
    "CRM_WEB_PORT": "15173",
    "INSIGHTS_WEB_PORT": "15174",
    "API_PORT": "18000",
    "ORCH_PORT": "18001",
    "MYSQL_HOST_PORT": "13307",
}

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")


def _config(tmp_path, values: dict[str, str]) -> dict:
    """``docker compose config`` with only ``values`` set: no .env file, no inherited ports."""
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    env = {key: value for key, value in os.environ.items() if key not in PORT_VARIABLES}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(env_file),
            "--profile",
            "collect",
            "config",
            "--format",
            "json",
        ],
        env={**env, **values},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["services"]


def _published(services: dict) -> dict[str, list[str]]:
    return {
        name: [str(port["published"]) for port in service.get("ports", [])]
        for name, service in services.items()
        if service.get("ports")
    }


def test_with_no_values_the_old_ports_are_published(tmp_path):
    services = _config(tmp_path, {})
    assert _published(services) == {
        "crm-web": ["5173"],
        "insights-web": ["5174"],
        "api": ["8000"],
        "orchestrator": ["8001"],
        "db": ["3307"],
    }
    assert services["crm-web"]["environment"]["VITE_API_URL"] == "http://localhost:8000"
    assert services["insights-web"]["environment"]["VITE_ORCH_URL"] == "http://localhost:8001"


def test_beside_v1_publishes_its_own_ports_and_the_apps_follow_them(tmp_path):
    services = _config(tmp_path, BESIDE_V1)
    assert _published(services) == {
        "crm-web": ["15173"],
        "insights-web": ["15174"],
        "api": ["18000"],
        "orchestrator": ["18001"],
        "db": ["13307"],
    }
    targets = {
        name: [port["target"] for port in service.get("ports", [])]
        for name, service in services.items()
    }
    assert targets["crm-web"] == [5173] and targets["db"] == [3306]
    assert services["crm-web"]["environment"]["VITE_API_URL"] == "http://localhost:18000"
    assert services["insights-web"]["environment"]["VITE_ORCH_URL"] == "http://localhost:18001"
    assert "http://localhost:15173" in services["api"]["environment"]["CORS_ORIGINS"]
    assert "http://localhost:15174" in services["orchestrator"]["environment"]["CORS_ORIGINS"]


def test_the_collector_only_collects(tmp_path):
    collector = _config(tmp_path, {"COLLECT_SECONDS": "120"})["collector"]
    assert collector["profiles"] == ["collect"]
    assert collector["command"] == ["python", "-m", "sdlc.signals.github", "run", "--every", "120"]
    assert collector["environment"]["ORCHESTRATOR_MODE"] == "off"
    assert "ports" not in collector
