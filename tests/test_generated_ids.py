import copy
import json
import unittest

from jsonschema import Draft202012Validator

from test_core import english_document
from test_beat_transitions import continued_story
from vc_director.core.errors import VCError
from vc_director.core.planner.identifiers import resolve_generated_ids
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.utilities import canonical
from test_core import FakeBackend
from vc_director.core.artifacts.validation import validate_story


class GeneratedIDTests(unittest.TestCase):
    def test_collision_renames_trigger_and_is_idempotent(self):
        old = english_document()["scenes"][0]
        scene = copy.deepcopy(old)
        scene["id"] = "later"
        action = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "action")
        # Dialogue identities stay immutable and are not part of this collision.
        for shot in scene["shots"]:
            shot["events"] = [e for e in shot["events"] if e["kind"] != "dialogue"]
        scene["sound_events"] = [dict(id="effect", trigger_event_id=action["id"])]
        prior_action = action["id"]
        text = action["text"]
        mapping = resolve_generated_ids(scene, [old])
        self.assertEqual(scene["sound_events"][0]["trigger_event_id"],
                         next(item["new_id"] for item in mapping if item["old_id"] == prior_action))
        self.assertEqual(action["text"], text)
        self.assertEqual(resolve_generated_ids(scene, [old]), [])

    def test_dialogue_and_ambiguous_local_duplicates_are_rejected(self):
        old = english_document()["scenes"][0]
        scene = copy.deepcopy(old)
        scene["id"] = "later"
        with self.assertRaisesRegex(VCError, "dialogue ID"):
            resolve_generated_ids(scene, [old])
        scene = copy.deepcopy(old)
        action = next(event for event in scene["shots"][0]["events"] if event["kind"] == "action")
        scene["shots"][0]["events"].append(copy.deepcopy(action))
        scene["sound_events"] = [dict(id="sound", trigger_event_id=action["id"])]
        original = copy.deepcopy(scene)
        with self.assertRaisesRegex(VCError, "Ambiguous trigger_event_id"):
            resolve_generated_ids(scene, [])
        self.assertEqual(scene, original)

    def test_unreferenced_camera_duplicates_get_unique_ids_without_content_or_timing_changes(self):
        scenario, enhanced, outlines, document = continued_story()
        scene = copy.deepcopy(document["scenes"][0])
        camera = copy.deepcopy(scene["shots"][0]["events"][0])
        extra = copy.deepcopy(scene["shots"][0])
        extra.update(id=scene["shots"][0]["id"], events=[camera])
        scene["shots"].append(extra)
        original = copy.deepcopy(scene)
        changes = resolve_generated_ids(scene, [])
        self.assertEqual(len(changes), 2)
        ids = [s["id"] for s in scene["shots"]] + [e["id"] for s in scene["shots"] for e in s["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        for old_shot, shot in zip(original["shots"], scene["shots"]):
            self.assertEqual(dict(shot, id=old_shot["id"], events=old_shot["events"]), old_shot)
            for old, new in zip(old_shot["events"], shot["events"]):
                self.assertEqual(dict(new, id=old["id"]), old)
        self.assertEqual(resolve_generated_ids(scene, []), [])

    def test_trigger_binds_event_even_when_a_shot_and_sound_use_the_same_id(self):
        scene = copy.deepcopy(english_document()["scenes"][0])
        action = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "action")
        old = action["id"]
        scene["shots"][0]["id"] = old
        scene["sound_events"] = [dict(id=old, trigger_event_id=old)]
        resolve_generated_ids(scene, [])
        self.assertEqual(scene["sound_events"][0]["trigger_event_id"], action["id"])
        self.assertEqual(len({scene["shots"][0]["id"], action["id"], scene["sound_events"][0]["id"]}), 3)

    def test_generated_ids_cannot_steal_authored_dialogue_even_if_visited_first(self):
        scene = copy.deepcopy(english_document()["scenes"][0])
        dialogue = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue")
        original = copy.deepcopy(dialogue)
        scene["shots"][0]["id"] = dialogue["id"]
        resolve_generated_ids(scene, [])
        self.assertNotEqual(scene["shots"][0]["id"], dialogue["id"])
        self.assertEqual(dialogue, original)
        scene["shots"][0]["events"].append(copy.deepcopy(dialogue))
        before = copy.deepcopy(scene)
        with self.assertRaisesRegex(VCError, "dialogue ID reused"):
            resolve_generated_ids(scene, [])
        self.assertEqual(scene, before)

    def test_generated_event_cannot_take_a_future_authored_dialogue_id(self):
        scene = copy.deepcopy(english_document()["scenes"][0])
        event = next(e for sh in scene['shots'] for e in sh['events'] if e['kind'] == 'action')
        event['id'] = 'future_dialogue'
        original = copy.deepcopy(event)
        resolve_generated_ids(scene, [], authored_dialogue_ids=['future_dialogue'])
        self.assertNotEqual(event['id'], 'future_dialogue')
        self.assertEqual(dict(event, id=original['id']), original)

    def test_planner_accepts_cross_scene_generated_collision(self):
        scenario, enhanced, outlines, document = continued_story()
        scenes = copy.deepcopy(document["scenes"])
        old_id = scenes[0]["shots"][0]["events"][0]["id"]
        scenes[2]["shots"][0]["events"][0]["id"] = old_id
        backend = FakeBackend(["SCENES\t1\t" + canonical(dict(scenes=items)) for items in
                               (outlines[:2], outlines[2:])] +
                              ["DETAIL\t1\t" + canonical(scene) for scene in scenes])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(len(backend.requests), 5)
        ids = [e["id"] for sc in result["scenes"] for sh in sc["shots"] for e in sh["events"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_planner_accepts_local_camera_duplicate_in_disjoint_shots_without_retry(self):
        scenario, enhanced, outlines, document = continued_story()
        scene = document['scenes'][0]
        first = scene['shots'][0]
        second = copy.deepcopy(first)
        first['end_ms'] = 5000
        for event in first['events']:
            event['end_ms'] = 5000
        second.update(id='second_shot', start_ms=5000,
                      events=[dict(second['events'][0], start_ms=5000)])
        scene['shots'].append(second)
        source = copy.deepcopy(scene)
        backend = FakeBackend(['SCENES\t1\t' + canonical(dict(scenes=items)) for items in
                               (outlines[:2], outlines[2:])] +
                              ['DETAIL\t1\t' + canonical(scene) for scene in document['scenes']])
        runner = StageRunner(backend)
        actual = StoryTimelinePlanner(runner).plan(scenario, enhanced)
        self.assertEqual(len(backend.requests), 5)
        fixed = actual['scenes'][0]
        for old_shot, new_shot in zip(source['shots'], fixed['shots']):
            for old, new in zip(old_shot['events'], new_shot['events']):
                self.assertEqual(dict(new, id=old['id']), old)
        self.assertTrue(any(row['stage'] == 'generated_ids' for row in runner.trace))

    def test_local_camera_collision_does_not_mask_shot_coverage_recovery(self):
        scenario, enhanced, outlines, document = continued_story()
        good = document['scenes'][0]
        good.update(visual_directions=[], visible_actor_ids=good['actor_ids'][:])
        overlap = copy.deepcopy(good)
        extra = copy.deepcopy(overlap['shots'][0])
        extra['events'] = extra['events'][:1]
        overlap['shots'].append(extra)
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outlines[0],
                       previous_scenes=[], locked_scene=None)
        class Backend(FakeBackend):
            def complete_record(self, system, request, record_type, *, payload_schema):
                response = self.complete(system, request)
                Draft202012Validator(payload_schema).validate(json.loads(response.split('\t', 2)[2]))
                return response
        backend = Backend(['DETAIL\t1\t' + canonical(s) for s in (overlap, good)])
        def check(scene):
            resolve_generated_ids(scene, [])
            candidate = copy.deepcopy(document)
            candidate['scenes'][0] = scene
            validate_story(candidate)
        actual = StageRunner(backend).run('detail', good['id'], payload, check)
        self.assertEqual(actual, good)
        self.assertEqual(backend.requests[1]['errors'][0]['code'], 'shot_coverage')
