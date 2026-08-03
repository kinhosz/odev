"""Regression tests for reusing a repository's primary checkout as the worktree for its currently
checked-out branch, instead of always forcing a dedicated worktree under odev's home directory.
"""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import PropertyMock, patch

from git import Repo

from odev.common.connectors.base import Connector
from odev.common.connectors.git import GitConnector
from odev.common.odoobin import OdoobinProcess


class FakeOdev:
    """Minimal stand-in for the framework's `Odev` instance, only exposing `worktrees_path`."""

    def __init__(self, worktrees_path: Path):
        self.worktrees_path = worktrees_path


class FakeOdoobinProcess:
    """Minimal stand-in exposing only what the properties under test rely on."""

    def __init__(self, repositories, worktree_name):
        self._repositories = repositories
        self._worktree = worktree_name

    @property
    def odoo_repositories(self):
        return iter(self._repositories)

    @property
    def worktree(self):
        return self._worktree


# Reuse the exact property/method implementations from OdoobinProcess.
FakeOdoobinProcess.odoo_worktrees = OdoobinProcess.odoo_worktrees
FakeOdoobinProcess.clone_repositories = OdoobinProcess.clone_repositories


class TestWorktreeReuse(TestCase):
    """Ensure a repository already checked out on the requested branch is reused as-is."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        root = Path(self.tmpdir.name)

        odev_patch = patch.object(
            Connector, "odev", new_callable=PropertyMock, return_value=FakeOdev(root / "unused-worktrees-dir")
        )
        odev_patch.start()
        self.addCleanup(odev_patch.stop)

        remote_path = root / "remote.git"
        Repo.init(remote_path, bare=True)

        seed_path = root / "seed"
        seed = Repo.clone_from(remote_path, seed_path)
        (seed_path / "README.md").write_text("master\n")
        seed.index.add(["README.md"])
        seed.index.commit("initial commit on master")
        seed.remote().push("master")

        seed.git.checkout("-b", "19.0")
        (seed_path / "README.md").write_text("19.0\n")
        seed.index.add(["README.md"])
        seed.index.commit("commit on 19.0")
        seed.remote().push("19.0")

        self.base_path = root / "base"
        self.base_repo = Repo.clone_from(remote_path, self.base_path)
        self.base_repo.git.checkout("19.0")
        self.base_repo.git.branch("--set-upstream-to", "origin/19.0", "19.0")

        self.connector = GitConnector("acme/odoo", path=self.base_path)

    def test_matching_branch_is_reused_without_new_worktree(self):
        """A repository whose primary checkout is already on the requested branch must be reused directly."""
        process = FakeOdoobinProcess([self.connector], worktree_name="19.0")
        worktrees = list(process.odoo_worktrees)

        self.assertEqual(len(worktrees), 1)
        self.assertEqual(worktrees[0].path, self.base_path)
        self.assertEqual(worktrees[0].branch, "19.0")

    def test_mismatched_branch_yields_no_worktree(self):
        """If the requested version differs from what's checked out, the primary checkout must not match."""
        process = FakeOdoobinProcess([self.connector], worktree_name="18.0")
        worktrees = list(process.odoo_worktrees)

        self.assertEqual(worktrees, [])

    def test_clone_repositories_does_not_reset_existing_checkout(self):
        """clone_repositories() must not force-checkout 'master', which would disrupt an actively used clone."""
        process = FakeOdoobinProcess([self.connector], worktree_name="19.0")
        process.clone_repositories()

        self.assertEqual(self.base_repo.active_branch.name, "19.0")
