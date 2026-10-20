"""The delegation protocol reaches every Hermes `delegate_task` child (#2174).

The `coding-handling` harness requires the protocol in every delegated or
reviewer prompt, runtime-native spawns included. OMH's own unit prompts carried
it; a Hermes-native child got it only when the parent model pasted it in. The
plugin now registers the `omh.delegation-protocol` system prompt section,
which renders for a child (`platform == "subagent"`) and is empty for every
other session, so the parent's own prompt does not change.

Everything except the single-definition check reaches the section through
`register()`, so on a tree without it these tests fail on what the plugin
registers, not on a missing import.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402
from omh.coding import unit_prompt_protocol  # noqa: E402
from omh.plugin_bundle.omh import register  # noqa: E402
from omh.plugin_bundle.omh.hooks import tool_hooks  # noqa: E402
from omh.skills.catalog import harness_definition  # noqa: E402

SECTION_ID = "omh.delegation-protocol"
# Hermes' own limits (`hermes_cli/plugins_dispatch.py`), copied because the
# host is not importable here.
_HOST_MAX_SECTION_CHARS = 4000
_HOST_MAX_SECTIONS_TOTAL_CHARS = 8000
_HOST_SECTIONS_CONTAINER = "<!-- hermes-plugin-sections:start -->\n" + "\n<!-- hermes-plugin-sections:end -->"
# The composition-guide entries a delegated agent acts on. `prompt_cache` and
# `applies_to` tell the composing parent how to write a delegate prompt.
_CHILD_PROTOCOL_KEYS = ("goal_echo", "verification_stop", "review_cap")


def _session_info(platform: str, session_id: str = "s-1") -> dict[str, str]:
    return {
        "session_id": session_id,
        "model": "gpt-6-astra",
        "provider": "openai-codex",
        "platform": platform,
        "profile_name": "default",
        "cwd": "/tmp/project-a",
    }


def _host_framed(section_id: str, text: str) -> str:
    return f"## Plugin Context: {section_id}\n<!-- hermes-plugin-section-chars:{len(text)} -->\n\n{text}"


def _composition_guide_protocol() -> dict[str, str]:
    status, stdout, _stderr = run_cli(["coding", "composition-guide", "--json"])
    if status != 0:
        raise AssertionError(f"composition-guide exited {status}")
    return json.loads(stdout)["delegation_protocol"]


class _SectionCtx:
    def __init__(self) -> None:
        self.sections: list[dict[str, object]] = []

    def register_tool(self, name, *args, **kwargs) -> None:
        pass

    def register_hook(self, name, callback) -> None:
        pass

    def register_system_prompt_section(self, id, content, *, position="after_memory", max_chars=4000):
        self.sections.append({"id": id, "content": content, "position": position, "max_chars": max_chars})


class _Manager:
    def __init__(self) -> None:
        self._system_prompt_sections: dict[str, object] = {}


class _ManagedSectionCtx(_SectionCtx):
    """A plugin-loader context that refuses a second registration, as Hermes does.

    Hermes logs that refusal on every session init, so an attempt is recorded
    even when it is refused.
    """

    def __init__(self, manager: _Manager) -> None:
        super().__init__()
        self._manager = manager
        self.attempts: list[str] = []

    def register_system_prompt_section(self, id, content, **kwargs):
        self.attempts.append(id)
        if id in self._manager._system_prompt_sections:
            raise ValueError(f"system prompt section {id!r} is already registered by plugin 'omh'")
        self._manager._system_prompt_sections[id] = content
        super().register_system_prompt_section(id, content, **kwargs)


def _registered_section(ctx: _SectionCtx) -> dict[str, object]:
    found = [section for section in ctx.sections if section["id"] == SECTION_ID]
    if len(found) != 1:
        raise AssertionError(f"expected one {SECTION_ID} registration, got {len(found)}")
    return found[0]


def _render(platform: object, session_id: str = "s-1") -> str:
    """What the registered section renders for a session on `platform`."""
    ctx = _SectionCtx()
    register(ctx)
    content = _registered_section(ctx)["content"]
    info = _session_info(platform, session_id) if isinstance(platform, str) else platform
    return content(info)


class ProducerParityTests(unittest.TestCase):
    def test_a_child_section_carries_every_composition_guide_protocol_line(self) -> None:
        protocol = _composition_guide_protocol()
        text = _render("subagent")
        for key in _CHILD_PROTOCOL_KEYS:
            with self.subTest(key=key):
                self.assertIn(protocol[key], text)

    def test_the_control_plane_reads_the_bundle_definitions(self) -> None:
        from omh.plugin_bundle.omh import delegation_protocol

        for name in ("GOAL_ECHO_PROTOCOL", "VERIFICATION_STOP_PROTOCOL", "REVIEW_ROLE_PROTOCOL"):
            with self.subTest(name=name):
                self.assertIs(getattr(unit_prompt_protocol, name), getattr(delegation_protocol, name))


class FramingTests(unittest.TestCase):
    """The section also reaches children no OMH-guided parent composed, such as
    Hermes' own `/review` reviewer, so only the goal echo-back is unconditional."""

    def test_only_the_goal_echo_applies_to_every_child(self) -> None:
        protocol = _composition_guide_protocol()
        lines = _render("subagent").splitlines()
        self.assertIn(f"- {protocol['goal_echo']}", lines)
        self.assertIn(f"- When your task changes files: {protocol['verification_stop']}", lines)
        self.assertIn(
            f"- When you review work against stated success criteria: {protocol['review_cap']}", lines
        )


class RegistrationTests(unittest.TestCase):
    def test_the_plugin_registers_the_section_and_it_renders_for_a_child(self) -> None:
        ctx = _SectionCtx()
        register(ctx)
        section = _registered_section(ctx)
        from omh.plugin_bundle.omh import delegation_protocol

        self.assertEqual(section["position"], "after_memory")
        text = section["content"](_session_info("subagent"))
        self.assertEqual(text, delegation_protocol.DELEGATION_PROTOCOL_SECTION)
        # The host skips a section longer than its `max_chars`.
        self.assertLessEqual(len(text.strip()), int(section["max_chars"]))
        self.assertLessEqual(int(section["max_chars"]), _HOST_MAX_SECTION_CHARS)

    def test_both_omh_sections_together_leave_most_of_the_shared_budget(self) -> None:
        ctx = _SectionCtx()
        register(ctx)
        _registered_section(ctx)
        info = _session_info("subagent")
        blocks = [_host_framed(str(s["id"]), s["content"](info)) for s in sorted(ctx.sections, key=lambda s: s["id"])]
        rendered = len(_HOST_SECTIONS_CONTAINER) + len("\n\n".join(blocks))
        self.assertLess(rendered, _HOST_MAX_SECTIONS_TOTAL_CHARS // 2)

    def test_a_second_loader_pass_does_not_register_it_again(self) -> None:
        manager = _Manager()
        first = _ManagedSectionCtx(manager)
        register(first)
        second = _ManagedSectionCtx(manager)
        register(second)
        self.assertEqual([s["id"] for s in first.sections].count(SECTION_ID), 1)
        self.assertNotIn(SECTION_ID, second.attempts)


class RenderTests(unittest.TestCase):
    def test_each_child_section_carries_the_protocol_once(self) -> None:
        protocol = _composition_guide_protocol()
        texts = [_render("subagent", f"child-{index}") for index in range(3)]
        self.assertEqual(len(set(texts)), 1)
        for text in texts:
            for key in _CHILD_PROTOCOL_KEYS:
                with self.subTest(key=key):
                    self.assertEqual(text.count(protocol[key]), 1)

    def test_the_parent_and_every_other_session_render_nothing(self) -> None:
        self.assertNotEqual(_render("subagent"), "")
        for platform in ("cli", "tui", "telegram", "discord", "cron", "desktop", "", "SUBAGENT", " subagent"):
            with self.subTest(platform=platform):
                self.assertEqual(_render(platform), "")
        self.assertEqual(_render({"session_id": "s-1"}), "")
        self.assertEqual(_render(None), "")


class DelegateToolCallTests(unittest.TestCase):
    """The section is the delivery; a `delegate_task` call's own arguments stay the model's."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.omh_home = Path(tmp.name) / "omh"
        self.hermes_home = Path(tmp.name) / "hermes"
        self.omh_home.mkdir()
        self.hermes_home.mkdir()

    def pre_tool_call(self, tool_name: str, args: dict[str, object]) -> object:
        return tool_hooks.pre_tool_call(
            tool_name=tool_name,
            args=args,
            session_id="parent-1",
            task_id="parent-1",
            omh_home=str(self.omh_home),
            hermes_home=str(self.hermes_home),
        )

    def test_no_tool_call_has_its_arguments_rewritten(self) -> None:
        calls = (
            ("delegate_task", {"goal": "Print the word hello."}),
            ("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}]}),
            ("terminal", {"command": "ls"}),
            ("read_file", {"path": "README.md"}),
        )
        for tool_name, args in calls:
            with self.subTest(tool_name=tool_name, args=args):
                result = self.pre_tool_call(tool_name, args)
                self.assertFalse(isinstance(result, dict) and result.get("action") == "modify", result)


class HarnessGuidanceTests(unittest.TestCase):
    """The parent stops pasting the protocol where the plugin already carries it."""

    def test_coding_handling_leaves_hermes_children_to_the_plugin(self) -> None:
        lines = [line for line in harness_definition("coding-handling").quality_bar if "composition-guide" in line]
        self.assertEqual(len(lines), 1, lines)
        line = lines[0]
        self.assertIn("On Hermes `delegate_task` the OMH plugin puts the protocol in each child's system prompt", line)
        self.assertIn("do not paste it into the goal", line)
        self.assertIn("still write the numbered done criteria there", line)
        self.assertIn("For Codex, Claude Code, and other runtime-native spawns, embed it in the prompt yourself", line)


if __name__ == "__main__":
    unittest.main()
