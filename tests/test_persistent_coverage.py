import copy
import json
import unittest

from jsonschema import Draft202012Validator

from test_core import fixture, with_camera
from test_scene_roles import story
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical


class PersistentCoverageTests(unittest.TestCase):
    def test_shot_constraint_survives_an_unrelated_vocal_error_and_resets_for_next_scene(self):
        doc, _ = story()
        for audio in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            audio["effects"] = "expand"
        good = with_camera(doc["scenes"][0])
        outline = fixture("outline-response")["records"][0]["payload"]["scenes"][0]
        outline["actor_ids"] = good["actor_ids"]
        scenario = {k: copy.deepcopy(v) for k, v in doc.items()
                    if k not in {"schema", "scenario_sha256", "scenes", "required_events"}}
        scenario.update(schema="VCD_SCENARIO_V1", source_sha256=doc["scenario_sha256"])
        enhanced = fixture("enhanced-story")
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outline, previous_scenes=[], locked_scene=None)
        overlap = copy.deepcopy(good)
        extra = copy.deepcopy(overlap["shots"][0])
        extra.update(id="duplicate_shot", events=[dict(extra["events"][0], id="duplicate_camera")])
        overlap["shots"].append(extra)
        vocal = copy.deepcopy(good)
        action = next(e for e in vocal["shots"][0]["events"] if e["kind"] == "action")
        vocal["sound_events"] = [dict(id="laugh", kind="effect", start_ms=0, end_ms=1000,
            trigger_event_id=action["id"], text="笑い声")]

        class Backend:
            def __init__(self):
                self.responses = iter([overlap, vocal, good, good])
                self.requests, self.schemas = [], []
            def identity(self):
                return {"test": "persistent-coverage"}
            def complete_record(self, system, request, record_type, *, payload_schema):
                self.requests.append(json.loads(request))
                self.schemas.append(payload_schema)
                response = copy.deepcopy(next(self.responses))
                # Every mocked response also obeys the actual decoder schema.
                Draft202012Validator(payload_schema).validate(response)
                return "DETAIL\t1\t" + canonical(response)

        def check(scene):
            candidate = copy.deepcopy(doc)
            candidate["scenes"] = [scene]
            validate_story(candidate)

        backend = Backend()
        runner = StageRunner(backend)
        result = runner.run("detail", good["id"], payload, check)
        self.assertEqual(result, good)
        self.assertEqual([r["retry"] for r in backend.requests], ["initial", "repair", "isolated"])
        self.assertEqual(backend.requests[2]["errors"][0]["code"], "vocal_cue")
        self.assertIn("shot_coverage", [e["code"] for e in backend.requests[2]["errors"]])
        for schema in backend.schemas[1:]:
            shots = schema["$defs"]["scene"]["properties"]["shots"]
            self.assertEqual((shots["minItems"], shots["maxItems"]), (1, 1))
            self.assertFalse(Draft202012Validator(schema).is_valid(overlap))
            invisible_action = copy.deepcopy(good)
            event = next(e for e in invisible_action["shots"][0]["events"] if e["kind"] == "action")
            event["actor_id"] = "caller"
            self.assertFalse(Draft202012Validator(schema).is_valid(invisible_action))
            self.assertEqual(schema["$defs"]["scene"]["properties"]["visible_actor_ids"], {"const": ["hero"]})
            # The off-screen participant remains a legal authored speaker.
            self.assertEqual(next(e for e in good["shots"][0]["events"] if e["kind"] == "dialogue")["actor_id"], "caller")
        self.assertTrue(Draft202012Validator(backend.schemas[0]).is_valid(overlap))
        # Constraint state belongs to this run, not to the runner or next Scene.
        runner.run("detail", good["id"], payload, lambda value: None)
        self.assertNotIn("maxItems", backend.schemas[3]["$defs"]["scene"]["properties"]["shots"])
        self.assertEqual(backend.requests[3]["errors"], [])


if __name__ == "__main__":
    unittest.main()
