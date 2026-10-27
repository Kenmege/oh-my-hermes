"""The rendered-scene lane of `frontend` and `visual-qa`, and the routing that reaches it.

Two agents built the same voxel pagoda garden from one prompt. Both consoles
were clean; the one judged less colorful booted on flat noon under a
"golden hour" subtitle, and its birds read as floating black cubes. Nothing in
the catalog named a 3D or canvas scene, so neither the build nor its own
visual review had a check for either. These tests pin the clauses that make a
scene's first frame reviewable, the capture rule in `visual-qa`, and the
phrase triggers, including that the everyday words inside them do not widen
the skill.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from omh.skill_pack import builtin_skill_templates
from omh.skills.catalog import builtin_definitions
from omh.skills.catalog_portable import PORTABLE_REFERENCE_PATHS
from omh.skills.catalog_types import omh_skill_display_name
from omh.skills.packaging import builtin_skill_reference_templates
from omh.skills.render import rendered_scene_reference_templates
from omh.wrapper.contract import build_chat_interaction_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE = ("frontend", "references/rendered-scene-craft.md")


def _unwrapped(content: str) -> str:
    return " ".join(content.split())


def _reference() -> str:
    for template in builtin_skill_reference_templates():
        if (template.skill_name, template.relative_path) == SCENE:
            return _unwrapped(template.content)
    raise AssertionError(f"{SCENE} is not packaged")


def _body(skill: str) -> str:
    for template in builtin_skill_templates():
        if template.name == skill:
            return _unwrapped(template.content)
    raise AssertionError(f"missing skill {skill}")


def _route(message: str) -> dict:
    return build_chat_interaction_payload(message, source="discord")["route"]


class RegistryTests(unittest.TestCase):
    def test_the_producer_emits_the_reference(self) -> None:
        produced = {(t.skill_name, t.relative_path) for t in rendered_scene_reference_templates()}
        self.assertEqual(produced, {SCENE})

    def test_it_is_packaged_generated_and_portable(self) -> None:
        packaged = {(t.skill_name, t.relative_path): t.content for t in builtin_skill_reference_templates()}
        self.assertIn(SCENE, packaged)
        self.assertIn(f"{SCENE[0]}/{SCENE[1]}", PORTABLE_REFERENCE_PATHS)
        path = REPO_ROOT / "skills" / omh_skill_display_name(SCENE[0]) / SCENE[1]
        self.assertTrue(path.exists(), path)
        self.assertEqual(path.read_text(encoding="utf-8"), packaged[SCENE])

    def test_both_bodies_point_at_it(self) -> None:
        frontend = _body("frontend")
        self.assertIn("references/rendered-scene-craft.md", frontend)
        self.assertIn("A clean console is not a finished scene", frontend)
        visual_qa = _body("visual-qa")
        self.assertIn("omh-frontend/references/rendered-scene-craft.md", visual_qa)
        self.assertIn("capture the first frame exactly as booted", visual_qa)


class SceneCraftTests(unittest.TestCase):
    def test_the_first_frame_keeps_the_scenes_own_promise(self) -> None:
        content = _reference()
        self.assertIn("## The first frame is the product", content)
        self.assertIn("Boot on the view the scene's own words promise", content)
        self.assertIn("a count the HUD prints matches what the scene draws", content)
        self.assertIn("The hero subject is framed whole at the default camera", content)

    def test_light_comes_before_geometry(self) -> None:
        content = _reference()
        self.assertIn("More geometry does not fix a flat frame; light does", content)
        for clause in (
            "One key light with a colour temperature chosen for the time of day",
            "hemisphere-style light",
            "Tone mapping set explicitly",
            "Shadows from the key light",
            "fog or depth haze coloured from the sky gradient",
            "need value separation",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, content)

    def test_small_things_read_as_what_they_are(self) -> None:
        content = _reference()
        self.assertIn("A single dark cube reads as floating debris, even when the code calls it a bird", content)
        self.assertIn("Pure black or unlit instances are a defect until the code shows they were intended", content)
        self.assertIn("Advertised quantities are visible quantities", content)

    def test_the_capture_matrix_and_the_evidence_boundary(self) -> None:
        content = _reference()
        for capture in (
            "the first frame exactly as booted, before any input",
            "each named camera preset or tour stop",
            "every time of day the scene advertises",
            "a settled frame and a motion frame",
        ):
            with self.subTest(capture=capture):
                self.assertIn(capture, content)
        self.assertIn("A frame rate is reported only with the device class and resolution", content)
        self.assertIn("prepared_not_observed", content)

    def test_the_reference_stays_engine_neutral(self) -> None:
        # "three.js-style" names a family of engines; an API call, a vendor, or
        # one engine's constant would make the contract someone else's stack.
        content = _reference().lower()
        for name in ("acesfilmic", "meshstandardmaterial", "babylon", "unity", "unreal", "r169"):
            with self.subTest(name=name):
                self.assertNotIn(name, content)


class RoutingTests(unittest.TestCase):
    def test_the_phrases_are_triggers(self) -> None:
        frontend = next(d for d in builtin_definitions() if d.name == "frontend")
        for phrase in ("three.js scene", "threejs scene", "webgl scene", "voxel scene", "voxel art scene"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, frontend.triggers)

    def test_scene_requests_dispatch_frontend(self) -> None:
        for message in (
            "Design and create a very creative, elaborate, and detailed voxel art scene of a pagoda in a beautiful garden.",
            "Make a WebGL scene with a day-night cycle.",
            "Polish the lighting in our three.js scene, it looks flat.",
            "Our threejs scene looks washed out; give it more atmosphere.",
        ):
            with self.subTest(message=message):
                route = _route(message)
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "frontend"))

    def test_everyday_senses_do_not_name_frontend(self) -> None:
        for message in (
            "Voxel-based morphometry of the MRI scans shows atrophy.",
            "The crime scene photos were filed.",
            "Our art scene in Berlin is growing.",
            "Three of us will review the scene.",
            "Update three.js to the latest version in package.json.",
        ):
            with self.subTest(message=message):
                route = _route(message)
                self.assertNotEqual(route["action"], "dispatch")
                self.assertNotEqual(route["candidate_skill"], "frontend")
                self.assertNotIn("frontend", [row["skill"] for row in route.get("recommendations") or []][:1])


if __name__ == "__main__":
    unittest.main()
