"""Tests for Odoo venv preparation (setuptools / gevent / --no-build-isolation)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import odev.common as odev_common
from odev.common.odoobin import OdoobinProcess
from odev.common.version import OdooVersion


def _repo_root() -> Path:
    """Git root of the odev project (contains the inner `odev` package and `tests/`)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _static_requirements_path() -> Path:
    """Path to odev's static requirements.txt."""
    return _repo_root() / "odev" / "static" / "requirements.txt"


class TestStaticRequirementsSetuptools(unittest.TestCase):
    """Guardrails for #93: setuptools must be new enough for pip + --no-build-isolation sdists."""

    def test_static_requirements_setuptools_floor_for_python_3_8_plus(self):
        """Python >= 3.8 must pin setuptools>=69 (see odev/static/requirements.txt)."""
        text = _static_requirements_path().read_text(encoding="utf-8")
        self.assertIn("setuptools>=69.0.0", text)
        self.assertRegex(
            text,
            r"setuptools>=69\.0\.0;\s*python_version\s*>?=\s*['\"]3\.8['\"]",
        )

    @staticmethod
    def _markers_target_python_3_8_or_later(text: str) -> bool:
        return "python_version >= '3.8'" in text or 'python_version >= "3.8"' in text

    def test_static_requirements_no_old_setuptools_cap_for_modern_python(self):
        """Ensure we do not reintroduce setuptools<59 for 3.8-3.11 (breaks gevent builds)."""
        for line in _static_requirements_path().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "setuptools" not in stripped:
                continue
            if sys.version_info >= (3, 8) and self._markers_target_python_3_8_or_later(stripped):
                self.assertNotRegex(
                    stripped,
                    r"setuptools\s*>=\s*58[^.]|setuptools\s*>=\s*58\.0\.0,\s*<59",
                    msg=f"Unexpected legacy setuptools cap on supported Python: {stripped!r}",
                )


class TestPrepareVenvBootstrap(unittest.TestCase):
    """prepare_venv must install a modern setuptools before --no-build-isolation gevent."""

    def setUp(self):
        super().setUp()
        self._req_path = Path("/tmp/odev-prepare-venv-req.txt")  # noqa: S108
        self._prev_framework = odev_common.framework
        self._mock_odev = MagicMock()
        self._mock_odev.home_path = Path("/tmp/odev-prepare-venv-test-home")  # noqa: S108
        odev_common.framework = self._mock_odev

    def tearDown(self):
        odev_common.framework = self._prev_framework
        super().tearDown()

    def _make_process(self, mock_venv: MagicMock) -> OdoobinProcess:
        db = MagicMock()
        db.exists = True
        db.edition = "community"
        db.repository = None
        process = OdoobinProcess(db)
        process.with_version(OdooVersion("16.0"))
        process._venv = mock_venv
        return process

    def test_new_venv_installs_setuptools_floor_before_pyyaml(self):
        mock_venv = MagicMock()
        mock_venv.exists = False
        mock_venv.missing_requirements.return_value = iter([])

        process = self._make_process(mock_venv)

        with patch.object(
            OdoobinProcess, "addons_requirements", new_callable=PropertyMock, return_value=[self._req_path]
        ):
            process.prepare_venv()

        mock_venv.create.assert_called_once()
        mock_venv.install_packages.assert_any_call(
            ["wheel", "setuptools>=69.0.0", "pip", "cython<3.0.0"],
        )
        mock_venv.install_packages.assert_any_call(
            ["pyyaml==5.4.1"],
            ["--no-build-isolation"],
        )

    def test_missing_gevent_upgrades_setuptools_before_no_build_isolation(self):
        mock_venv = MagicMock()
        mock_venv.exists = True
        mock_venv.missing_requirements.side_effect = [
            iter(["gevent==21.8.0 ; python_version >= '3.7'"]),
            iter([]),
        ]

        process = self._make_process(mock_venv)

        with patch.object(
            OdoobinProcess, "addons_requirements", new_callable=PropertyMock, return_value=[self._req_path]
        ):
            process.prepare_venv()

        mock_venv.create.assert_not_called()
        calls = mock_venv.install_packages.call_args_list
        self.assertGreaterEqual(len(calls), 2, msg="Expected setuptools/wheel then gevent installs")
        self.assertEqual(
            calls[0].args,
            (["setuptools>=69.0.0", "wheel"], []),
        )
        self.assertEqual(
            calls[1].args,
            (["gevent==21.8.0"], ["--no-build-isolation"]),
        )
