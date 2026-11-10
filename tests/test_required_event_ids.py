import copy
import unittest

from jsonschema import Draft202012Validator

from test_core import FakeBackend, fixture, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.artifacts.identifiers import resolve_required_event_ids
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.direction import StoryEnhancer
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical


def dialogue_story():
    scenario = fixture('scenario')
    scenario['beats'][0]['dialogues'] = copy.deepcopy(fixture()['beats'][0]['dialogues'])
    scenario['beats'][0]['audio']['vocal'] = scenario['audio']['vocal'] = 'dialogue'
    return scenario, fixture('enhanced-story')


class RequiredEventIDTests(unittest.TestCase):
    def test_authored_dialogue_collision_is_resolved_before_outlines(self):
        scenario, enhanced = dialogue_story()
        authored = copy.deepcopy(scenario)
        dialogue = scenario['beats'][0]['dialogues'][0]
        enhanced['beats'][0]['required_events'][0]['id'] = dialogue['id']
        event_before = copy.deepcopy(enhanced['beats'][0]['required_events'][0])
        backend = FakeBackend(['ENHANCED\t1\t' + canonical(enhanced)])
        with temporary_directory() as directory:
            runner = StageRunner(backend, directory)
            fixed = StoryEnhancer(runner).enhance(scenario)
            event = fixed['beats'][0]['required_events'][0]
            self.assertNotEqual(event['id'], dialogue['id'])
            self.assertTrue(event['id'].startswith('req_'))
            self.assertEqual(dict(event, id=event_before['id']), event_before)
            self.assertEqual(StoryEnhancer(runner).enhance(scenario), fixed)
            self.assertEqual(len(backend.requests), 1)
        self.assertEqual(scenario, authored)

    def test_generated_collisions_preserve_records_sources_and_existing_free_ids(self):
        scenario, enhanced, _, _ = continued_story()
        first, second = [b['required_events'][0] for b in enhanced['beats']]
        first['id'] = scenario['beats'][0]['id']
        second['id'] = first['id']
        spare = dict(copy.deepcopy(first), id='req_return_1')
        enhanced['beats'][0]['required_events'].append(spare)
        original = copy.deepcopy(enhanced)
        mapping = resolve_required_event_ids(scenario, enhanced)
        self.assertEqual(len(mapping), 2)
        self.assertEqual(spare['id'], 'req_return_1')
        self.assertNotEqual(first['id'], spare['id'])
        events = [e for b in enhanced['beats'] for e in b['required_events']]
        self.assertEqual(len({e['id'] for e in events}), 3)
        for old_beat, new_beat in zip(original['beats'], enhanced['beats']):
            self.assertEqual(len(old_beat['required_events']), len(new_beat['required_events']))
            for old, new in zip(old_beat['required_events'], new_beat['required_events']):
                self.assertEqual(dict(new, id=old['id']), old)
        self.assertEqual(resolve_required_event_ids(scenario, enhanced), [])

    def test_planner_allocates_ids_from_old_enhanced_cache_before_detail(self):
        scenario, enhanced, outlines, document = continued_story()
        # Old caches may contain an otherwise valid generated requirement
        # using the authored beat ID. Bypass Enhancer to exercise this boundary.
        old = enhanced['beats'][0]['required_events'][0]['id']
        enhanced['beats'][0]['required_events'][0]['id'] = scenario['beats'][0]['id']
        original = copy.deepcopy(enhanced)
        prepared = copy.deepcopy(enhanced)
        resolve_required_event_ids(scenario, prepared)
        new = prepared['beats'][0]['required_events'][0]['id']
        for outline, scene in zip(outlines, document['scenes']):
            outline['required_event_ids'] = [new if id == old else id for id in outline['required_event_ids']]
            scene['required_event_ids'] = outline['required_event_ids'][:]
        backend = FakeBackend(['SCENES\t1\t' + canonical(dict(scenes=items))
                               for items in (outlines[:2], outlines[2:])] +
                              ['DETAIL\t1\t' + canonical(s) for s in document['scenes']])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(validate_story(result), result)
        self.assertEqual(enhanced, original)
        self.assertEqual(result['beats'], scenario['beats'])
        self.assertEqual(result['required_events'][0]['id'], new)
        self.assertEqual(backend.requests[0]['payload']['enhanced'], prepared)

    def test_generation_schema_forbids_dialogue_id_as_requirement_id(self):
        scenario, enhanced = dialogue_story()
        validator = Draft202012Validator(generation_schema('enhance', dict(scenario=scenario, image_facts=[])))
        self.assertTrue(validator.is_valid(enhanced))
        enhanced['beats'][0]['required_events'][0]['id'] = scenario['beats'][0]['dialogues'][0]['id']
        self.assertFalse(validator.is_valid(enhanced))

    def test_authored_duplicates_are_still_rejected_with_the_exact_id(self):
        scenario, _ = dialogue_story()
        dialogue = scenario['beats'][0]['dialogues'][0]
        dialogue['id'] = scenario['beats'][0]['id']
        with self.assertRaises(VCError) as caught:
            validate_story(scenario)
        self.assertEqual(caught.exception.diagnostic.path, 'source_ids')
        self.assertIn(repr(dialogue['id']), str(caught.exception))


if __name__ == '__main__':
    unittest.main()
