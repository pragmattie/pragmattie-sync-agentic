import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
CADDYFILE = ROOT / "proxy" / "Caddyfile"
PORT_VARIABLES = (
    "CRM_WEB_PORT",
    "INSIGHTS_WEB_PORT",
    "API_PORT",
    "ORCH_PORT",
    "MYSQL_HOST_PORT",
    "PROXY_PORT",
    "COLLECT_SECONDS",
)
BESIDE_V1 = {
    "CRM_WEB_PORT": "15173",
    "INSIGHTS_WEB_PORT": "15174",
    "API_PORT": "18000",
    "ORCH_PORT": "18001",
    "MYSQL_HOST_PORT": "13307",
    "PROXY_PORT": "18080",
}

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")


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


@needs_docker
def test_with_no_values_the_old_ports_are_published(tmp_path):
    services = _config(tmp_path, {})
    assert _published(services) == {
        "crm-web": ["5173"],
        "insights-web": ["5174"],
        "api": ["8000"],
        "orchestrator": ["8001"],
        "db": ["3307"],
        "proxy": ["80"],
    }
    assert services["crm-web"]["environment"]["VITE_API_URL"] == "http://localhost:8000"
    assert services["insights-web"]["environment"]["VITE_ORCH_URL"] == "http://localhost:8001"


@needs_docker
def test_beside_v1_publishes_its_own_ports_and_the_apps_follow_them(tmp_path):
    services = _config(tmp_path, BESIDE_V1)
    assert _published(services) == {
        "crm-web": ["15173"],
        "insights-web": ["15174"],
        "api": ["18000"],
        "orchestrator": ["18001"],
        "db": ["13307"],
        "proxy": ["18080"],
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
    api_origins = services["api"]["environment"]["CORS_ORIGINS"].split(",")
    orch_origins = services["orchestrator"]["environment"]["CORS_ORIGINS"].split(",")
    assert "http://pragmattie-sync.localhost:18080" in api_origins
    assert "http://insights.pragmattie-sync.localhost:18080" in orch_origins


@needs_docker
def test_the_proxy_serves_both_web_apps_and_starts_with_the_stack(tmp_path):
    services = _config(tmp_path, {})
    proxy = services["proxy"]
    assert proxy["image"] == "caddy:2.10-alpine"
    assert "profiles" not in proxy
    assert proxy["restart"] == "unless-stopped"
    assert set(proxy["depends_on"]) == {"crm-web", "insights-web"}
    assert [port["target"] for port in proxy["ports"]] == [80]
    (volume,) = proxy["volumes"]
    assert volume["source"] == str(CADDYFILE)
    assert volume["target"] == "/etc/caddy/Caddyfile"
    assert volume["read_only"] is True
    assert "http://pragmattie-sync.localhost" in services["api"]["environment"][
        "CORS_ORIGINS"
    ].split(",")
    assert "http://insights.pragmattie-sync.localhost" in services["orchestrator"]["environment"][
        "CORS_ORIGINS"
    ].split(",")


def test_the_caddyfile_routes_each_name_to_its_app():
    text = CADDYFILE.read_text()
    sites = dict(re.findall(r"^(http://\S+) \{\n\s*reverse_proxy (\S+)\n\}", text, re.M))
    assert sites == {
        "http://pragmattie-sync.localhost": "crm-web:5173",
        "http://insights.pragmattie-sync.localhost": "insights-web:5174",
    }
    assert "auto_https off" in text


@pytest.mark.parametrize(
    ("app", "name"),
    [
        ("crm-web", "pragmattie-sync.localhost"),
        ("insights-web", "insights.pragmattie-sync.localhost"),
    ],
)
def test_each_vite_dev_server_allows_its_proxy_name(app, name):
    config = (ROOT / "apps" / app / "vite.config.js").read_text()
    assert re.search(r"allowedHosts: \[[^\]]*\"" + re.escape(name) + r"\"", config)


@needs_docker
def test_the_collector_only_collects(tmp_path):
    collector = _config(tmp_path, {"COLLECT_SECONDS": "120"})["collector"]
    assert collector["profiles"] == ["collect"]
    assert collector["command"] == ["python", "-m", "sdlc.signals.github", "run", "--every", "120"]
    assert collector["environment"]["ORCHESTRATOR_MODE"] == "off"
    assert "ports" not in collector


@needs_docker
def test_the_collector_plans_only_when_planner_mode_is_on(tmp_path):
    unset = {"PLANNER_MODE": "", "PLANNER_MODEL": ""}
    environment = _config(tmp_path, unset)["collector"]["environment"]
    assert environment["PLANNER_MODE"] == "off"
    assert "ANTHROPIC_API_KEY" in environment  # read only when PLANNER_MODE is on
    assert environment["PLANNER_MODEL"] == "claude-sonnet-5"
    environment = _config(tmp_path, {"PLANNER_MODE": "on"})["collector"]["environment"]
    assert environment["PLANNER_MODE"] == "on"
