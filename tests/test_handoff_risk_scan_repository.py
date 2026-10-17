from __future__ import annotations

import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from _handoff_risk_fixture import git, invoke, repository
from omh.quality import handoff_risk_repository as metadata


class HandoffRiskRepositoryTests(unittest.TestCase):
    def test_S4_scan_error_when_promised_tree_missing_without_fetching(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: a real promisor remote, but only a disposable local repository.
            source = repository(Path(root))
            clone = Path(root) / "clone"
            _ = git(source, "clone", "--local", "--no-hardlinks", str(source), str(clone))
            _ = git(clone, "config", "remote.origin.promisor", "true")
            tree = git(clone, "rev-parse", "HEAD^{tree}").decode().strip()
            (clone / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
            before = {str(path.relative_to(clone)): path.read_bytes() for path in clone.rglob("*") if path.is_file()}
            # When
            code, report = invoke(["--repo", str(clone)])
            # Then: missing tree metadata is an error, never permission to fetch.
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "scan_error")
            self.assertIsNone(report["verdict"])
            self.assertEqual(before, {str(path.relative_to(clone)): path.read_bytes() for path in clone.rglob("*") if path.is_file()})

    def test_S5_error_when_git_output_pipe_overflows(self):
        # Given: a real child, only the Git process-launch boundary is replaced.
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 2097152)"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # When
        with patch("omh.quality.handoff_risk_repository.subprocess.Popen", return_value=process):
            code, report = invoke(["--repo", "."])
        # Then
        self.assertEqual(code, 2)
        self.assertEqual(report["error_category"], "metadata_oversized")
        self.assertIsNotNone(process.returncode)

    def test_S5_error_when_git_process_timeout(self):
        # Given: the child waits for input that nobody sends; no timing-luck sleep.
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # When: time itself is the bounded subprocess behavior under test.
        with patch("omh.quality.handoff_risk_repository.subprocess.Popen", return_value=process), patch.object(metadata, "_GIT_TIMEOUT", 0):
            code, report = invoke(["--repo", "."])
        # Then
        self.assertEqual(code, 2)
        self.assertEqual(report["error_category"], "repository_timeout")
        self.assertIsNotNone(process.returncode)

    def test_S4_unchanged_when_repository_has_executable_clean_filter(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: a real Git filter would fail if any content hash were requested.
            repo = repository(Path(root))
            (repo / ".env").write_text("synthetic")
            git(repo, "add", ".env")
            git(repo, "commit", "-m", "secret-path")
            git(repo, "config", "filter.risk-fixture.clean", "exit 97")
            git(repo, "config", "filter.risk-fixture.required", "true")
            (repo / ".gitattributes").write_text("* filter=risk-fixture\n")
            (repo / ".env").write_text("SYNTHETIC_CHANGED_SECRET")
            before = {str(path.relative_to(repo)): path.read_bytes() for path in repo.rglob("*") if path.is_file()}
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["input_summary"]["dirty_count"], 1)
            self.assertEqual(before, {str(path.relative_to(repo)): path.read_bytes() for path in repo.rglob("*") if path.is_file()})

    def test_S5_error_when_metadata_limits_exceeded(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = repository(Path(root))
            real_git = metadata._git
            for payload in (b"x\0" * 10001, b"x" * 1048577):
                with self.subTest(size=len(payload)):
                    def supplied(path: Path, args: tuple[str, ...]) -> tuple[int, bytes]:
                        if args == ("ls-files", "--others", "--exclude-standard", "-z"):
                            return 0, payload
                        return real_git(path, args)
                    # When
                    with patch.object(metadata, "_git", supplied):
                        code, report = invoke(["--repo", str(repo)])
                    # Then
                    self.assertEqual(code, 2)
                    self.assertEqual(report["error_category"], "metadata_oversized")
                    self.assertIsNone(report["verdict"])

    def test_S5_clear_when_git_listings_exceed_budget_but_kept_metadata_fits(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: a real clean repository whose tree and index listings together pass
            # 1 MiB, the raw sum the scanner used to charge, while far under 10000 entries.
            repo = repository(Path(root))
            directory = repo / ("a" * 70) / ("b" * 60)
            directory.mkdir(parents=True)
            for number in range(2300):
                _ = (directory / f"{number:04d}.txt").write_bytes(b"x")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "wide")
            listings = git(repo, "ls-tree", "-r", "-z", "HEAD") + git(repo, "ls-files", "--stage", "--debug", "-z")
            self.assertGreater(len(listings), metadata.MAX_METADATA_BYTES)
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["verdict"], "clear")
            self.assertEqual(report["input_summary"]["tracked_count"], 2301)

    def test_S5_boundary_when_retained_metadata_fills_budget_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: the fixture's real tree listing plus supplied tree-only entries that
            # fill the documented charge to the byte: HEAD and branch output, then each
            # tree path with its mode and object id. "tracked.txt" is in both the tree and
            # the index, so it is charged once.
            repo = repository(Path(root))
            real_git = metadata._git
            listing = ("ls-tree", "-r", "-z", "HEAD")
            committed = git(repo, *listing)
            header, path = committed[:-1].split(b"\t")
            mode, _, oid = header.split(b" ")
            room = (metadata.MAX_METADATA_BYTES - len(git(repo, "rev-parse", "--verify", "HEAD"))
                    - len(git(repo, "symbolic-ref", "--quiet", "--short", "HEAD"))
                    - (len(path) + len(mode) + 1 + len(oid)))
            charge = 100 + len(mode) + 1 + len(oid)
            count, spare = divmod(room, charge)
            for extra, (status, category) in ((0, ("completed", None)), (1, ("scan_error", "metadata_oversized"))):
                with self.subTest(extra=extra):
                    paths = [b"%06d" % number + b"q" * 94 for number in range(count - 1)]
                    paths.append(b"last" + b"q" * (96 + spare + extra))
                    payload = committed + b"".join(mode + b" blob " + oid + b"\t" + item + b"\0" for item in paths)

                    def supplied(path: Path, args: tuple[str, ...]) -> tuple[int, bytes]:
                        return (0, payload) if args == listing else real_git(path, args)
                    # When
                    with patch.object(metadata, "_git", supplied):
                        _, report = invoke(["--repo", str(repo)])
                    # Then: the supplied entries are missing from the index, so a completed
                    # report is advisory; one more byte is over the budget.
                    self.assertEqual((report["status"], report["error_category"]), (status, category))

    def test_S5_error_when_listing_exceeds_retained_or_entry_limit(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: Git-shaped listings within the read cap; only their content is supplied.
            repo = repository(Path(root))
            real_git = metadata._git
            oid = b"0" * 40
            stat = b"  ctime: 0:0\n  mtime: 0:0\n  dev: 0\tino: 0\n  uid: 0\tgid: 0\n  size: 0\tflags: 0\n"
            # 9100 paths of 116 bytes: past 1 MiB, under 10000 entries, short enough to lstat.
            long_paths = [b"%05d/" % number + b"p" * 110 for number in range(9100)]
            tree = ("ls-tree", "-r", "-z", "HEAD")
            index = ("ls-files", "--stage", "--debug", "-z")
            others = ("ls-files", "--others", "--exclude-standard", "-z")
            # Each case names the last Git command the scan may still run.
            cases = {
                "tree_bytes": (tree, tree, b"".join(b"100644 blob " + oid + b"\t" + path + b"\0" for path in long_paths)),
                "index_bytes": (index, others, b"".join(b"100644 " + oid + b" 0\t" + path + b"\0" + stat for path in long_paths)),
                "index_entries": (index, others, b"".join(b"100644 " + oid + b" 0\tp%05d\0" % number + stat for number in range(10001))),
            }
            for name, (listing, last, payload) in cases.items():
                with self.subTest(name):
                    calls: list[tuple[str, ...]] = []

                    def supplied(path: Path, args: tuple[str, ...]) -> tuple[int, bytes]:
                        calls.append(args)
                        return (0, payload) if args == listing else real_git(path, args)
                    # When
                    with patch.object(metadata, "_git", supplied):
                        code, report = invoke(["--repo", str(repo)])
                    # Then: the scan stops at the listing that crossed the limit.
                    self.assertEqual(code, 2)
                    self.assertEqual(report["error_category"], "metadata_oversized")
                    self.assertIsNone(report["verdict"])
                    self.assertEqual(calls[-1], last)

    def test_S5_error_when_index_listing_overflows_read_cap_and_child_reaped(self):
        with tempfile.TemporaryDirectory() as root:
            # Given: a real repository; only the index listing comes from a real child. At
            # the cap it writes and exits; one byte past it, it then waits for input nobody
            # sends, so only a kill ends it.
            repo = repository(Path(root))
            launch_git = subprocess.Popen
            cases = ((metadata._LISTING_BYTES, "", "repository_invalid"),
                     (metadata._LISTING_BYTES + 1, "; sys.stdin.buffer.read()", "metadata_oversized"))
            for size, then, category in cases:
                with self.subTest(size=size):
                    script = "import sys; sys.stdout.buffer.write(b'x' * %d); sys.stdout.flush()%s" % (size, then)
                    with subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
                        def launch(argv: list[str], **options: object) -> object:
                            # The scanner's own `with` reaps the child and every forwarded Git.
                            return child if "--stage" in argv else launch_git(argv, **options)
                        # When
                        with patch("omh.quality.handoff_risk_repository.subprocess.Popen", side_effect=launch):
                            code, report = invoke(["--repo", str(repo)])
                        # Then: a listing at the cap is read whole and parsed (and this one has
                        # no record); past it the read stops, and the child is killed and reaped
                        # rather than timed out.
                        self.assertEqual(code, 2)
                        self.assertEqual(report["error_category"], category)
                        self.assertIsNotNone(child.returncode)

    def test_S5_docs_state_the_listing_read_cap(self):
        # Given: the operator reference that states the read bounds.
        docs = (Path(__file__).resolve().parents[1] / "docs" / "HANDOFF-RISK-SCAN.md").read_text(encoding="utf-8")
        # Then: the number it gives is the cap the code enforces.
        self.assertIn(f"read up to\n{metadata._LISTING_BYTES} bytes", docs)

    def test_S4_environment_isolation_when_git_environment_poisoned(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = repository(Path(root))
            # When
            with patch.dict(os.environ, {"GIT_DIR": str(Path(root) / "foreign"), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "exit 97"}):
                code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["verdict"], "clear")

    def test_S3_dirty_when_index_change_or_deletion_or_unborn(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = repository(Path(root))
            (repo / "tracked.txt").write_text("staged")
            git(repo, "add", ".")
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["input_summary"]["dirty_count"], 1)

    def test_S3_clear_when_detached_head(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = repository(Path(root))
            git(repo, "checkout", "--detach")
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["verdict"], "clear")

    def test_S3_dirty_when_tracked_file_removed(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = repository(Path(root))
            (repo / "tracked.txt").unlink()
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["input_summary"]["dirty_count"], 1)

    def test_S3_clear_when_repository_unborn(self):
        with tempfile.TemporaryDirectory() as root:
            # Given
            repo = Path(root)
            git(repo, "init", "-b", "main")
            # When
            code, report = invoke(["--repo", str(repo)])
            # Then
            self.assertEqual(code, 0)
            self.assertEqual(report["verdict"], "clear")
