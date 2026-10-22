"""One path rule for the three domain-science toolkit scripts.

Each script under `src/skills/*_toolkit/scripts/` ships standalone into the
installed skill directory, so each carries its own copy of `system_alias`.
This holds the copies to one rule: macOS's own top-level `/etc`, `/tmp` and
`/var` links into `/private` are trusted, and no other symlink is. Without the
alias every macOS user's temp dir (`/var/folders/...`) and `/tmp` was refused.

The refusals run with `sys.platform` patched to darwin, so a Linux or Windows
run still exercises the macOS branch. `os.readlink` is patched for the alias
table because only root can make a top-level link to test against.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock


def assert_only_system_aliases_trusted(test, guard, system_alias, root, refusal, *, traversal=None):
    real = root / "input.json"
    real.write_bytes(b"{}")
    # On macOS the default temp dir sits under the /var alias, so `real` exercises it.
    for path in (real, Path("/tmp/omh-alias-probe.json")):
        with test.subTest(accepted=path):
            test.assertEqual(guard(path), path.absolute())
    link = root / "link.json"
    link.symlink_to(real)
    linked_dir = root / "linked-dir"
    linked_dir.symlink_to(root, target_is_directory=True)
    (root / "private" / "var").mkdir(parents=True)
    lookalike = root / "var"
    lookalike.symlink_to(Path("private", "var"), target_is_directory=True)
    refused = [(link, refusal), (linked_dir / "input.json", refusal), (lookalike / "input.json", refusal)]
    if traversal is not None:
        refused.append((root / ".." / root.name / "input.json", traversal))
    with mock.patch("sys.platform", "darwin"):
        for path, message in refused:
            with test.subTest(refused=path), test.assertRaisesRegex(ValueError, message):
                guard(path)
    cases = (
        ("darwin", "/tmp", "private/tmp", True),
        ("darwin", "/var", "/private/var", True),
        ("darwin", "/etc", "private/etc", True),
        ("darwin", "/tmp", "private/var", False),
        ("darwin", "/var", "../private/var", False),
        ("darwin", "/opt", "private/opt", False),
        ("darwin", str(lookalike), "private/var", False),
        ("linux", "/var", "private/var", False),
    )
    for platform, part, link_text, trusted in cases:
        with test.subTest(platform=platform, part=part, link=link_text), \
                mock.patch("sys.platform", platform), mock.patch("os.readlink", return_value=link_text):
            test.assertIs(system_alias(Path(part)), trusted)
