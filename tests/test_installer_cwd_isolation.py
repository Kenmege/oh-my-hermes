"""Installers must not execute modules supplied by cwd or PYTHONPATH."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "install.sh"


class InstallerIsolationContractTests(unittest.TestCase):
    def test_every_module_invocation_is_isolated(self) -> None:
        # Match shell commands, PowerShell argument arrays, and printed fallback
        # commands. Also accept combined Python options such as -Im.
        module_option = re.compile(r"(?<![\w-])-[IEP]*m\b")
        for name in ("install.sh", "install.ps1"):
            source = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            invocations = 0
            for number, line in enumerate(source.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                start = 0
                for match in module_option.finditer(line):
                    invocations += 1
                    options = re.findall(r"(?<![\w-])-([A-Za-z]+)\b", line[start:match.end()])
                    flags = set("".join(options))
                    with self.subTest(installer=name, line=number):
                        self.assertTrue(
                            "I" in flags or {"P", "E"} <= flags,
                            f"{name}:{number}: unisolated Python module invocation: {line.strip()}",
                        )
                    start = match.end()
            self.assertGreaterEqual(invocations, 4, f"no module invocations found in {name}")

    def test_command_discovery_probes_are_isolated(self) -> None:
        shell = INSTALLER.read_text(encoding="utf-8")
        powershell = (PROJECT_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn('"$OMH_RUNTIME_PYTHON" -I - "$OMH_BIN_DIR"', shell)
        self.assertIn("-Arguments @('-I', $probeFile, $OmhBinDir, $scriptsDir)", powershell)


@unittest.skipIf(sys.platform == "win32", "install.sh is the POSIX installer; install.ps1 covers Windows")
class InstallerCwdIsolationTests(unittest.TestCase):
    def test_shell_installer_ignores_untrusted_modules(self) -> None:
        with TemporaryDirectory() as pip_tmp:
            # Direct-Python mode runs `<OMH_PYTHON> -m pip`, so it needs an
            # interpreter that has pip. sys.executable may be a pip-less venv
            # (e.g. under `uv run`), so build a throwaway venv with pip.
            pip_python, pip_skip = self._python_with_pip(Path(pip_tmp).resolve())
            self._run_isolation_cases(pip_python, pip_skip)

    def _python_with_pip(self, base: Path) -> tuple[str | None, str | None]:
        venv_dir = base / "pip-venv"
        created = subprocess.run(
            [sys.executable, "-I", "-m", "venv", str(venv_dir)],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if created.returncode != 0 or not python.exists():
            return None, f"cannot create a venv with pip (ensurepip unavailable?): {created.stderr.strip()}"
        probe = subprocess.run(
            [str(python), "-I", "-m", "pip", "--version"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if probe.returncode != 0:
            return None, f"venv has no usable pip: {probe.stderr.strip()}"
        return str(python), None

    def _run_isolation_cases(self, pip_python: str | None, pip_skip: str | None) -> None:
        for mode in ("venv", "python"):
            for injection in ("cwd", "PYTHONPATH"):
                with self.subTest(mode=mode, injection=injection), TemporaryDirectory() as tmp:
                    if mode == "python" and pip_python is None:
                        self.skipTest(f"direct-Python mode needs pip: {pip_skip}")
                    python = pip_python if mode == "python" else sys.executable
                    root = Path(tmp).resolve()
                    cwd = root / "cwd"
                    cwd.mkdir()
                    malicious = cwd if injection == "cwd" else root / "injected"
                    malicious.mkdir(exist_ok=True)
                    markers = [root / f"PWNED_{module}" for module in ("venv", "pip", "omh")]
                    for module, marker in zip(("venv", "pip", "omh"), markers):
                        (malicious / f"{module}.py").write_text(
                            "import pathlib\n"
                            f"pathlib.Path({str(marker)!r}).write_text('x', encoding='utf-8')\n"
                            "raise SystemExit(1)\n",
                            encoding="utf-8",
                        )
                    # No inherited OMH/Python configuration or release lookup.
                    # The nonexistent local package must fail, without importing
                    # the attack modules or installing anything into the host.
                    env = {
                        "PATH": os.defpath,
                        "HOME": str(root / "home"),
                        "TMPDIR": str(root),
                        "OMH_PYTHON": python,
                        "OMH_INSTALL_MODE": mode,
                        "OMH_VENV_DIR": str(root / "venv"),
                        "OMH_PACKAGE_URL": str(root / "nonexistent.whl"),
                        "OMH_PIP_ARGS": "",
                        "OMH_LINK_COMMAND": "0",
                        "OMH_RUN_SETUP": "0",
                        "OMH_RUN_DOCTOR": "0",
                        "OMH_PROVISION_PYTHON": "0",
                        "PIP_NO_INDEX": "1",
                        "NO_COLOR": "1",
                    }
                    if injection == "PYTHONPATH":
                        env["PYTHONPATH"] = str(malicious)
                    result = subprocess.run(
                        ["sh", str(INSTALLER)],
                        cwd=cwd,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=120,
                        check=False,
                    )
                    output = result.stdout + result.stderr
                    self.assertFalse(any(marker.exists() for marker in markers), output)
                    self.assertEqual(result.returncode, 1, output)
                    self.assertIn("nonexistent.whl", output)


if __name__ == "__main__":
    unittest.main()
