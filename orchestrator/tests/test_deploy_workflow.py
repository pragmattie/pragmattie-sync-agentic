"""The deploy workflow (6.5): proposed in ``ci/proposed/`` until a person moves it live."""

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROPOSED = ROOT / "ci" / "proposed" / "deploy.yml"
LIVE = ROOT / ".github" / "workflows" / "deploy.yml"


def _path() -> Path:
    return PROPOSED if PROPOSED.exists() else LIVE


def _workflow() -> dict:
    return yaml.safe_load(_path().read_text())


def test_it_runs_only_on_a_push_to_main():
    workflow = _workflow()
    assert workflow["name"] == "Deploy"
    assert workflow[True] == {"push": {"branches": ["main"]}}  # YAML 1.1 reads "on" as true
    assert workflow["permissions"] == {"contents": "read", "statuses": "read"}
    assert workflow["concurrency"] == {"group": "deploy-production", "cancel-in-progress": False}


def test_it_has_the_gate_then_the_no_op_deploy():
    jobs = _workflow()["jobs"]
    assert list(jobs) == ["release-gate", "deploy"]
    gate, deploy = jobs["release-gate"], jobs["deploy"]
    assert gate["name"] == "Release gate"
    assert gate["timeout-minutes"] == 60
    assert gate["outputs"] == {
        "environment": "${{ steps.gate.outputs.environment }}",
        "tier": "${{ steps.gate.outputs.tier }}",
    }
    step = gate["steps"][0]
    assert step["id"] == "gate"
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "SHA": "${{ github.sha }}"}
    assert deploy["name"] == "Deploy (no-op)"
    assert deploy["needs"] == "release-gate"
    assert deploy["environment"] == {"name": "${{ needs.release-gate.outputs.environment }}"}
    text = "\n".join(s["run"] for s in deploy["steps"])
    assert "No-op deploy of ${GITHUB_SHA::7}" in text
    assert "Nothing is hosted: replace this step to deploy for real." in text


def _gate(tmp_path: Path, line: str) -> tuple[int, dict[str, str], str]:
    """Run the gate step with a stub ``gh`` that answers ``line``; its exit code and outputs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in {"gh": f"echo '{line}'", "sleep": "exit 99"}.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    output = tmp_path / "output"
    output.touch()
    script = _workflow()["jobs"]["release-gate"]["steps"][0]["run"]
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "acme/widgets",
        "SHA": "abc1234",
    }
    done = subprocess.run(
        # GitHub runs a step with "bash -eo pipefail"
        ["bash", "-eo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    pairs = dict(row.split("=", 1) for row in output.read_text().splitlines())
    return done.returncode, pairs, done.stdout


@pytest.mark.parametrize(
    ("description", "environment"),
    [
        ("T3 · Release: checks passed; approve the deployment in GitHub", "production-signoff"),
        ("T3 · Shadow (would be pending). Held: open incident in pipeline", "production-signoff"),
        ("T2 · Release: checks passed", "production"),
        ("T0 · Release: deploys automatically", "production"),
    ],
)
def test_a_success_names_the_environment_and_t3_goes_to_sign_off(
    tmp_path, description, environment
):
    code, outputs, _ = _gate(tmp_path, f"success|{description}")
    assert code == 0
    assert outputs == {"tier": description[:2], "environment": environment}


def test_a_failure_stops_and_pending_keeps_waiting(tmp_path):
    code, outputs, stdout = _gate(tmp_path / "failed", "failure|T2 · Blocked: CI failed")
    assert code == 1 and outputs == {}
    assert "::error::Release gate: T2 · Blocked: CI failed" in stdout
    # the stub sleep exits 99, so a waiting loop shows up as that code after one round
    code, outputs, stdout = _gate(tmp_path / "held", "pending|T2 · Held: waiting for CI")
    assert code == 99 and outputs == {}
    assert "held: T2 · Held: waiting for CI" in stdout
