"""Install helpers, service startup, and shell script syntax."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

import service
from conftest import PAKT_CACHE, ROOT, needs_pakt

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


def run_python(args: list, env: dict, *flags) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("DOC_GYM_", "PYTHON"))}
    return subprocess.run([sys.executable, *flags, *args], cwd=ROOT / "scripts", env={**base, **env},
                          capture_output=True, text=True, timeout=30)


def test_pakt_loads_from_the_vendor_dir(tmp_path):
    pkg = tmp_path / "vendor" / "pakt"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for name in ("document", "review", "sources", "structure", "styleguide"):
        (pkg / f"{name}.py").write_text("")
    code = "import signal_guide, sys; p = signal_guide.load_pakt(); print(p.__file__); print(sys.path[0])"
    # -S skips site-packages, so only the vendor dir can provide pakt.
    result = run_python(["-c", code], {"DOC_GYM_VENDOR": str(tmp_path / "vendor")}, "-S")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(pkg / "__init__.py"), str(tmp_path / "vendor")]


def test_missing_pakt_is_a_clear_error(tmp_path):
    code = "import signal_guide\ntry:\n    signal_guide.load_pakt()\nexcept Exception as e:\n    print(e)"
    result = run_python(["-c", code], {"DOC_GYM_VENDOR": str(tmp_path / "no-vendor")}, "-S")
    assert "PAKT is not available" in result.stdout and "ops/vendor-pakt.sh" in result.stdout


@needs_pakt
def test_grading_never_imports_anthropic(tmp_path, vault):
    trap = tmp_path / "trap" / "anthropic"
    trap.mkdir(parents=True)
    (trap / "__init__.py").write_text("raise ImportError('Doc Gym must not import anthropic')\n")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import runpy, sys\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        "assert 'anthropic' not in sys.modules\n")
    note = vault / "Doc Gym" / "note.md"
    note.write_text("---\nsignal_type: release_note\n---\n## Draft\n" + "You can now export hives as CSV files. " * 5)
    result = run_python(
        [str(runner), str(ROOT / "scripts" / "evaluate_draft.py"), str(note), "--dry-run"],
        {"PYTHONPATH": os.pathsep.join([str(trap.parent), str(ROOT / "scripts")]), "DOC_GYM_VAULT": str(vault),
         "PAKT_OFFLINE": "1", "PAKT_CACHE_DIR": PAKT_CACHE})
    assert result.returncode == 0, result.stderr
    assert "### Signal findings" in result.stdout


def test_pakt_lock_pins_a_full_commit():
    lock = dict(line.split("=", 1) for line in (ROOT / "pakt.lock").read_text().splitlines()
                if line and not line.startswith("#"))
    assert re.fullmatch(r"[0-9a-f]{40}", lock["PAKT_REF"])
    assert lock["PAKT_REPO"].startswith("https://github.com/")


def test_deploy_ships_pakt_and_the_bridge():
    deploy = (ROOT / "ops" / "deploy-server.sh").read_text()
    assert "vendor-pakt.sh" in deploy
    assert "signal_guide" in deploy
