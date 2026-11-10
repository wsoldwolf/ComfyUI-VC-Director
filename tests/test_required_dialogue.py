import copy
import importlib.util
import json
import unittest

from jsonschema import Draft202012Validator

from test_core import FakeBackend, english_document, fixture, with_camera
from test_beat_transitions import continued_story
from vc_director.core.artifacts.schema import schema
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.errors import VCError, require
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical


def speech_scene():
    document = english_document()
    scene = with_camera(document['scenes'][0])
    scene.update(visible_actor_ids=scene['actor_ids'][:], visual_directions=[])
    document['scenes'] = [scene]
    scenario = {k: copy.deepcopy(v) for k, v in document.items()
                if k not in {'schema', 'scenario_sha256', 'scenes', 'required_events'}}
    scenario.update(schema='VCD_SCENARIO_V1', source_sha256=document['scenario_sha256'])
    outline = {k: copy.deepcopy(scene[k]) for k in schema()['$defs']['scene_outline']['required']}
    payload = dict(scenario=scenario, enhanced=fixture('enhanced-story'), outline=outline,
                   previous_scenes=[], locked_scene=None,
                   required_dialogue_ids=[d['id'] for d in scenario['beats'][0]['dialogues']])
    return document, scene, payload


class RequiredDialogueTests(unittest.TestCase):
    def test_recovery_schema_requires_authored_speech_once_and_allows_interleaved_actions(self):
        document, scene, payload = speech_scene()
        second = copy.deepcopy(payload['scenario']['beats'][0]['dialogues'][0])
        second.update(id='dlg_second', text='「またね」')
        payload['scenario']['beats'][0]['dialogues'].append(second)
        payload['required_dialogue_ids'].append(second['id'])
        spoken = next(e for e in scene['shots'][0]['events'] if e['kind'] == 'dialogue')
        scene['shots'][0]['events'].append(dict(spoken, id=second['id'], dialogue_id=second['id'], text=second['text']))
        narrowed = generation_schema('detail', payload, errors=[dict(code='dialogue_coverage')])
        Draft202012Validator.check_schema(narrowed)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(scene))
        for id in payload['required_dialogue_ids']:
            removed = copy.deepcopy(scene)
            removed['shots'][0]['events'] = [e for e in removed['shots'][0]['events'] if e['id'] != id]
            self.assertFalse(validator.is_valid(removed))
        duplicate = copy.deepcopy(scene)
        duplicate['shots'][0]['events'].append(copy.deepcopy(spoken))
        self.assertFalse(validator.is_valid(duplicate))
        for key in ('id', 'dialogue_id', 'text', 'actor_id', 'language'):
            changed = copy.deepcopy(scene)
            next(e for e in changed['shots'][0]['events'] if e['kind'] == 'dialogue')[key] = 'changed'
            self.assertFalse(validator.is_valid(changed), key)
        if importlib.util.find_spec('llama_cpp'):
            from llama_cpp import LlamaGrammar
            grammar = record_grammar('DETAIL', payload_schema=narrowed)
            self.assertIn('-required-0', grammar)
            self.assertIn('-required-1', grammar)
            self.assertIn('-between', grammar)
            LlamaGrammar.from_string(grammar, verbose=False)

    def test_required_dialogue_constraint_survives_an_unrelated_error(self):
        document, good, payload = speech_scene()
        missing = copy.deepcopy(good)
        missing['shots'][0]['events'] = [e for e in missing['shots'][0]['events'] if e['kind'] != 'dialogue']
        vocal = copy.deepcopy(good)
        vocal['summary'] = 'The traveler whispers.'
        class Native(FakeBackend):
            def __init__(self):
                super().__init__(['DETAIL\t1\t' + canonical(s) for s in (missing, vocal, good)])
                self.schemas = []
            def complete_record(self, system, request, record_type, *, payload_schema):
                self.schemas.append(payload_schema)
                response = self.complete(system, request)
                Draft202012Validator(payload_schema).validate(json.loads(response.split('\t', 2)[2]))
                return response
        backend = Native()
        def check(scene):
            present = [e['dialogue_id'] for sh in scene['shots'] for e in sh['events'] if e['kind'] == 'dialogue']
            require(set(present) >= set(payload['required_dialogue_ids']), 'DIALOGUE_COVERAGE', scene['id'], 'Missing authored dialogue')
            candidate = copy.deepcopy(document)
            candidate['scenes'] = [scene]
            validate_story(candidate)
        result = StageRunner(backend).run('detail', good['id'], payload, check)
        self.assertEqual(result, good)
        self.assertEqual([r['retry'] for r in backend.requests], ['initial', 'repair', 'isolated'])
        self.assertEqual(backend.requests[2]['errors'][0]['code'], 'vocal_cue')
        for narrowed in backend.schemas[1:]:
            self.assertFalse(Draft202012Validator(narrowed).is_valid(missing))
            self.assertEqual(narrowed['$defs']['scene']['properties']['shots']['maxItems'], 1)
        self.assertIn('dialogue_coverage', [e['code'] for e in backend.requests[2]['errors']])

    def test_split_beat_assigns_only_remaining_dialogue_to_its_last_scene(self):
        scenario, enhanced, outlines, document = continued_story()
        template = english_document()['beats'][0]['dialogues'][0]
        dialogues = [dict(copy.deepcopy(template), id='dlg_first'), dict(copy.deepcopy(template), id='dlg_last')]
        scenario['beats'][0]['dialogues'] = dialogues
        scenario['beats'][0]['audio']['vocal'] = scenario['audio']['vocal'] = 'dialogue'
        event = next(e for sh in english_document()['scenes'][0]['shots'] for e in sh['events'] if e['kind'] == 'dialogue')
        for scene, dialogue in zip(document['scenes'][:2], dialogues):
            scene['audio']['vocal'] = 'dialogue'
            scene['shots'][0]['events'].append(dict(event, id=dialogue['id'], dialogue_id=dialogue['id'], start_ms=1000, end_ms=2000))
        backend = FakeBackend(['SCENES\t1\t' + canonical(dict(scenes=items)) for items in
                               (outlines[:2], outlines[2:])] +
                              ['DETAIL\t1\t' + canonical(s) for s in document['scenes']])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual([r['payload']['required_dialogue_ids'] for r in backend.requests[2:]], [[], ['dlg_last'], []])
        self.assertEqual(result['beats'][0]['dialogues'], dialogues)

    def test_assignment_rejects_unknown_or_already_used_dialogue(self):
        document, scene, payload = speech_scene()
        for change in ('unknown', 'used'):
            bad = copy.deepcopy(payload)
            if change == 'unknown':
                bad['required_dialogue_ids'] = ['unknown']
            else:
                bad['previous_scenes'] = [scene]
            with self.assertRaisesRegex(VCError, 'DIALOGUE_ASSIGNMENT'):
                generation_schema('detail', bad)


if __name__ == '__main__':
    unittest.main()
