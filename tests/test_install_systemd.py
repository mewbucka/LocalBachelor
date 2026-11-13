"""Tests for scripts/install_systemd.py (roadmap task 0.4 fixup).

Covers the pure renderer (render_unit) and the CLI: --dry-run writes
nothing; install / refusal / --force against an injected temp unit dir
(main() takes a unit_dir seam so these run without root, on any platform
the gate runs on). The subprocess --dry-run test additionally exercises
the real argparse entry point.
"""

import configparser
import importlib.util
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_systemd.py"
TEMPLATE = REPO_ROOT / "deploy" / "local-bachelor.service"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("install_systemd", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def install() -> types.ModuleType:
    return _load()


def _unit_dir(tmp_path: Path) -> Path:
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    return unit_dir


def test_render_unit_absolute_paths(install: types.ModuleType) -> None:
    unit = install.render_unit(
        repo_root="/opt/local-bachelor",
        python="/opt/local-bachelor/.venv/bin/python",
        config_path="/opt/local-bachelor/config.yaml",
    )
    assert "WorkingDirectory=/opt/local-bachelor" in unit
    assert (
        "ExecStart=/opt/local-bachelor/.venv/bin/python -m app.main "
        "--config /opt/local-bachelor/config.yaml" in unit
    )
    assert "Type=simple" in unit
    assert "Environment=LOG_LEVEL=info" in unit
    assert "Restart=on-failure" in unit


def test_render_unit_user_lines(install: types.ModuleType) -> None:
    kwargs = dict(
        repo_root="/opt/local-bachelor",
        python="/opt/local-bachelor/.venv/bin/python",
        config_path="/opt/local-bachelor/config.yaml",
    )
    with_user = install.render_unit(user="localbachelor", **kwargs)
    assert "User=localbachelor" in with_user
    assert "Group=localbachelor" in with_user
    without = install.render_unit(user=None, **kwargs)
    assert "User=" not in without
    assert "Group=" not in without


def test_render_unit_wants_multi_user(install: types.ModuleType) -> None:
    unit = install.render_unit(
        repo_root="/opt/local-bachelor",
        python="/opt/local-bachelor/.venv/bin/python",
        config_path="/opt/local-bachelor/config.yaml",
    )
    assert "WantedBy=multi-user.target" in unit
    assert "default.target" not in unit


def test_render_unit_parses_as_a_unit(install: types.ModuleType) -> None:
    unit = install.render_unit(
        repo_root="/opt/local-bachelor",
        python="/opt/local-bachelor/.venv/bin/python",
        config_path="/opt/local-bachelor/config.yaml",
        user="localbachelor",
    )
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(unit)
    assert parser.sections() == ["Unit", "Service", "Install"]
    assert parser["Service"]["User"] == "localbachelor"
    assert parser["Service"]["WorkingDirectory"] == "/opt/local-bachelor"
    assert parser["Service"]["ExecStart"].startswith("/opt/local-bachelor/")
    assert parser["Install"]["WantedBy"] == "multi-user.target"


def test_render_unit_contains_no_secrets(
    install: types.ModuleType, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        'secret_key: "SUPER-SECRET-SESSION-KEY-12345"\n'
        'settings_key: "SUPER-SECRET-FERNET-KEY-67890"\n',
        encoding="utf-8",
    )
    unit = install.render_unit(
        repo_root=str(tmp_path),
        python=str(tmp_path / ".venv" / "bin" / "python"),
        config_path=str(config),
        user="localbachelor",
    )
    # The unit references the config by path only; it never embeds values.
    assert f"--config {config}" in unit
    assert "SUPER-SECRET-SESSION-KEY-12345" not in unit
    assert "SUPER-SECRET-FERNET-KEY-67890" not in unit


def test_resolve_venv_python_absolute_and_existing(install: types.ModuleType) -> None:
    python = install.resolve_venv_python(install.REPO_ROOT)
    assert Path(python).is_absolute()
    assert Path(python).is_file()


def test_dry_run_prints_unit_and_writes_nothing(tmp_path: Path) -> None:
    name = f"lb-dry-{uuid.uuid4().hex[:8]}"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--name", name, "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    out = result.stdout
    assert "[Unit]" in out
    assert "[Service]" in out
    assert "[Install]" in out
    assert "WorkingDirectory=" in out
    assert "ExecStart=" in out
    assert "WantedBy=multi-user.target" in out
    assert "Traceback" not in result.stderr
    # --dry-run must not create the unit file anywhere.
    assert not (Path("/etc/systemd/system") / f"{name}.service").exists()


def test_install_writes_unit_and_prints_next_steps(
    install: types.ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit_dir = _unit_dir(tmp_path)
    rc = install.main(["--name", "lb-it", "--user", "localbachelor"], unit_dir=unit_dir)
    assert rc == 0
    target = unit_dir / "lb-it.service"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "User=localbachelor" in text
    assert "WantedBy=multi-user.target" in text
    out = capsys.readouterr().out
    assert f"installed {target}" in out
    assert "sudo systemctl daemon-reload" in out
    assert "sudo systemctl enable --now lb-it.service" in out


def test_install_refuses_to_overwrite_without_force(
    install: types.ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit_dir = _unit_dir(tmp_path)
    target = unit_dir / "lb-it.service"
    target.write_text("SENTINEL-OLD-UNIT\n", encoding="utf-8")
    rc = install.main(["--name", "lb-it", "--user", "localbachelor"], unit_dir=unit_dir)
    assert rc == 1
    assert target.read_text(encoding="utf-8") == "SENTINEL-OLD-UNIT\n"
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "--force" in err


def test_install_force_replaces_existing_unit(
    install: types.ModuleType, tmp_path: Path
) -> None:
    unit_dir = _unit_dir(tmp_path)
    target = unit_dir / "lb-it.service"
    target.write_text("SENTINEL-OLD-UNIT\n", encoding="utf-8")
    rc = install.main(
        ["--name", "lb-it", "--user", "localbachelor", "--force"],
        unit_dir=unit_dir,
    )
    assert rc == 0
    text = target.read_text(encoding="utf-8")
    assert "SENTINEL" not in text
    assert "WantedBy=multi-user.target" in text


def test_invalid_name_and_user_rejected(
    install: types.ModuleType, tmp_path: Path
) -> None:
    unit_dir = _unit_dir(tmp_path)
    assert install.main(["--name", "../evil"], unit_dir=unit_dir) == 2
    assert (
        install.main(["--name", "lb-it", "--user", "a\nb"], unit_dir=unit_dir) == 2
    )
    assert not any(unit_dir.iterdir())


def test_template_header_points_at_script() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:12])
    assert "template" in head.lower()
    assert "scripts/install_systemd.py" in head
    # The template body still carries the manual fallback.
    for section in ("[Unit]", "[Service]", "[Install]", "WantedBy="):
        assert section in text
