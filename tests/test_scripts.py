"""Install helpers, service startup, and shell script syntax."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

import service
from conftest import ROOT

SHELL_SCRIPTS = sorted([ROOT / "install.sh", *(ROOT / "ops").glob("*.sh")])
# Also look next to the interpreter, so a venv-installed shellcheck (shellcheck-py) is found.
SHELLCHECK = shutil.which("shellcheck", path=os.pathsep.join([os.path.dirname(sys.executable), os.environ["PATH"]]))


def run_script(name: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *map(str, args)],
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Doc Gym").mkdir(parents=True)
    return v


def test_service_refuses_to_start_without_token():
    with pytest.raises(SystemExit, match="DOC_GYM_TOKEN is not set"):
        service.main()


def test_write_client_config_creates_token_and_config(tmp_path, vault):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("# comment\nDOC_GYM_SERVICE_URLS=http://a.invalid:8765/, http://b.invalid:8765\n")
    result = run_script("write_client_config.py", vault, project)
    assert result.returncode == 0, result.stderr
    env_text = (project / ".env").read_text()
    token = env_text.split("DOC_GYM_TOKEN=")[1].strip()
    assert len(token) >= 32
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600
    config = json.loads((vault / "Doc Gym" / ".doc-gym.json").read_text())
    assert config == {"urls": ["http://a.invalid:8765", "http://b.invalid:8765"], "token": token}
    assert token not in result.stdout

    again = run_script("write_client_config.py", vault, project)
    assert again.returncode == 0
    assert (project / ".env").read_text() == env_text


def test_write_client_config_requires_service_urls(tmp_path, vault):
    project = tmp_path / "project"
    project.mkdir()
    result = run_script("write_client_config.py", vault, project)
    assert result.returncode != 0
    assert "DOC_GYM_SERVICE_URLS is not set" in result.stderr


def test_configure_templater_registers_hotkeys_and_drops_shell_commands(vault):
    cfg = vault / ".obsidian/plugins/templater-obsidian/data.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "templates_pairs": [["gym_new", "python3 x"], ["gym_eval", "python3 y"]],
        "enable_system_commands": True,
        "enabled_templates_hotkeys": ["", "Other.md"],
    }))
    result = run_script("configure_templater.py", vault)
    assert result.returncode == 0, result.stderr
    data = json.loads(cfg.read_text())
    assert data["templates_pairs"] == [["", ""]]
    assert data["enable_system_commands"] is False
    assert data["enabled_templates_hotkeys"] == [
        "Other.md", "Doc Gym/Templates/new-workout.md", "Doc Gym/Templates/evaluation-template.md"]
    assert len(list(cfg.parent.glob("data.json.bak-*"))) == 1


def test_configure_templater_keeps_unrelated_shell_commands(vault):
    cfg = vault / ".obsidian/plugins/templater-obsidian/data.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"templates_pairs": [["mine", "echo hi"]], "enable_system_commands": True}))
    assert run_script("configure_templater.py", vault).returncode == 0
    data = json.loads(cfg.read_text())
    assert data["templates_pairs"] == [["mine", "echo hi"]]
    assert data["enable_system_commands"] is True


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_script_syntax(script):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not SHELLCHECK, reason="shellcheck not installed")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shellcheck(script):
    result = subprocess.run([SHELLCHECK, "-S", "warning", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
