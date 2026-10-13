r"""Every shipped skill passes the Hermes install scanner across the supported range.

`hermes skills install <tap>/skills/<name>` runs `tools/skills_guard.py` over
the quarantined skill directory. For a community source a `dangerous` verdict
is refused outright (`--force` does not override it) and a `caution` verdict
is blocked too (`INSTALL_POLICY`), so one high finding refuses the install.
Twice a plain sentence in a generated skill body tripped a critical rule: a
cryptocurrency name in the shortlist sidecar (#2000), then `setgid`, a
`> .claude/settings` shape, "do not tell the user", a trigger spelling that
cryptocurrency name and a literal `sudo` across skill bodies and reference
files (#2014). The drift gates cannot see this -- a producer and its generated
file move together -- so this runs the scanner itself, vendored verbatim in
`tests/_vendor`, over `skills/` and `agent-skills/` exactly as Hermes runs it.

The scanner changes between Hermes releases and OMH installs on every release
from the `requires_hermes` floor up, so three versions run:

- v2 (`hermes_skills_guard_v2`): Hermes 0.21.1 through 0.21.3, the floor. Its
  `context_exfil` rule is `(include|output|print|send|share)\s+(?:\w+\s+)*`
  followed by `context` or `conversation`, with no word boundary on either
  end, so it spans a whole line of plain words and fires inside a word:
  "A blueprint is prepared design context" reads as `print ... context`. Four
  lines in ulw-loop, omh-wiki and omh-routing scanned `caution` under v2 alone
  until they were reworded.
- v8 (`hermes_skills_guard_v8`): Hermes 0.21.6.
- v10 (`hermes_skills_guard_v10`): Hermes 0.21.7 and upstream main. Upstream
  87a281e0c1 made a bare `setgid` and a `sudo` in Markdown informational
  (medium), so the pinned severities below differ per version.

Hermes 0.21.4 and 0.21.5 ship scanner v6, which is not vendored. Measured on
2026-10-13 by importing each copy: every v6 rule id is also a v2, v8 or v10
rule id. A regex diff was run as well: five v6 patterns match none of the
three byte for byte. `dns_exfil` is narrower than v2's and `bash_reverse_shell`
is equivalent to v8's, but `curl_pipe_shell`, `wget_pipe_shell` and
`echo_pipe_exec` lack the `\b` v8 added after the shell name, so v6 alone also
flags a pipe into a word that starts with `zsh`, `ksh` or `dash` (for example
`curl ... | dashboard`). This module cannot see that case.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _vendor import hermes_skills_guard_v2, hermes_skills_guard_v8, hermes_skills_guard_v10

REPO = Path(__file__).resolve().parents[1]
SHIPPED_SKILL_ROOTS = (REPO / "skills", REPO / "agent-skills")
SCANNERS = {
    "v2": hermes_skills_guard_v2,
    "v8": hermes_skills_guard_v8,
    "v10": hermes_skills_guard_v10,
}

# One sentence per rule that blocked, or under v2 would have blocked, an install.
BLOCKED_SENTENCES = {
    "setuid_setgid": "but on many Linux distributions `sg` is util-linux's setgid tool.",
    "deception_hide": "Do not tell the user an edit format will make an executor faster.",
    "other_agent_config_mod_shell": "project scope is `<dispatch cwd>/.claude/settings.local.json` with rules",
    "crypto_mining": "Strong routing signals: `monero gateway`, `connector readiness`",
    "sudo_usage": "Refused anywhere in argv: a shell or `env`, a forge CLI, `sudo`, network tools.",
    "context_exfil": "A blueprint is prepared design context. It is not evidence that a store was created.",
}

# The severity each version gives those sentences in a community SKILL.md. A
# rule a version does not fire on its sentence is absent from that row.
EXPECTED_SEVERITY = {
    "v2": {
        "setuid_setgid": "critical",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "high",
        "context_exfil": "high",
    },
    "v8": {
        "setuid_setgid": "critical",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "high",
    },
    "v10": {
        "setuid_setgid": "medium",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "medium",
    },
}


def _skill_dirs() -> list[Path]:
    return [path for root in SHIPPED_SKILL_ROOTS for path in sorted(root.iterdir()) if path.is_dir()]


def _blocking(result) -> list[str]:
    return [
        f"{finding.severity} {finding.pattern_id} {finding.file}:{finding.line}: {finding.match}"
        for finding in result.findings
        if finding.severity in ("critical", "high")
    ]


class HermesInstallScanTests(unittest.TestCase):
    def test_every_shipped_skill_scans_safe_as_a_community_install(self) -> None:
        problems: dict[str, list[str]] = {}
        for guard in SCANNERS.values():
            for skill_dir in _skill_dirs():
                result = guard.scan_skill(skill_dir, "community")
                blocking = _blocking(result)
                if result.verdict != "safe" or blocking:
                    key = f"{guard.SCANNER_VERSION} {skill_dir.relative_to(REPO)}"
                    problems[key] = [f"verdict={result.verdict}", *blocking]
        # assertEqual on dicts goes through assertDictEqual, whose repr and diff
        # truncation would hide the file:line findings this message exists for.
        report = "\n".join(f"{skill}: " + "; ".join(rows) for skill, rows in problems.items())
        self.assertFalse(
            problems,
            "a skill body trips a Hermes install-scanner rule; reword the producer in "
            "src/skills (never the generated file), regenerate, and re-derive the digests:\n" + report,
        )

    def test_a_caution_verdict_blocks_a_community_install_in_every_version(self) -> None:
        # Why the scan above refuses `caution`, not only `dangerous`.
        for version, guard in SCANNERS.items():
            with self.subTest(version=version):
                self.assertEqual(guard.INSTALL_POLICY["community"][guard.VERDICT_INDEX["caution"]], "block")

    def test_each_vendored_scanner_still_scores_the_sentences_that_blocked_installs(self) -> None:
        # Pins every vendored copy to the findings this gate exists for, so a
        # refresh that weakens or drops a rule cannot leave the scan above
        # passing vacuously.
        with TemporaryDirectory() as tmp:
            skill = Path(tmp) / "probe"
            skill.mkdir()
            (skill / "SKILL.md").write_text("\n".join(BLOCKED_SENTENCES.values()) + "\n", encoding="utf-8")
            results = {version: guard.scan_skill(skill, "community") for version, guard in SCANNERS.items()}
        for version, expected in EXPECTED_SEVERITY.items():
            with self.subTest(version=version):
                result = results[version]
                self.assertEqual(result.verdict, "dangerous")
                scored: dict[str, set[str]] = {}
                for finding in result.findings:
                    scored.setdefault(finding.pattern_id, set()).add(finding.severity)
                self.assertEqual(
                    {rule: scored.get(rule) for rule in expected},
                    {rule: {severity} for rule, severity in expected.items()},
                )


if __name__ == "__main__":
    unittest.main()
