import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import ROOT, english_document, compile_fixture, temporary_directory, with_camera
from vc_director.core.artifacts.assets import registry
from vc_director.core.artifacts.storage import save, load
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.h3_contract.runtime import conditioning_inputs
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.grammar import record_grammar
from jsonschema import Draft202012Validator


def prop_document():
    doc = english_document()
    doc['props'] = [dict(id='notebook', name='Notebook', description='A red cloth-covered notebook with rounded corners.',
                         size='148 mm wide, 210 mm tall, 10 mm thick.', image_asset_id='prop_sheet', reference_hint='The notebook in the upper left.'),
                    dict(id='pencil', name='Pencil', description='A yellow hexagonal wooden pencil.',
                         size='180 mm long; fits in one adult hand.', image_asset_id='prop_sheet', reference_hint='The yellow pencil on the right.')]
    doc['beats'][0]['prop_ids'] = ['notebook', 'pencil']
    doc['beats'][0]['text'] = '@hero picks up @notebook and @pencil.'
    scene = with_camera(doc['scenes'][0])
    scene.update(prop_ids=['notebook', 'pencil'], visible_prop_ids=['notebook', 'pencil'],
                 visible_actor_ids=['hero'], visual_directions=[], summary=doc['beats'][0]['text'])
    next(e for sh in scene['shots'] for e in sh['events'] if e['kind']=='action')['text'] = scene['summary']
    doc['scenes'] = [scene]
    return doc


class PropTagTests(unittest.TestCase):
    def test_scenario_tags_infer_props_and_scene_round_trip_preserves_sheet_selection_and_size(self):
        text = (ROOT / 'assets/emd/minimal.scenario.emd').read_text(encoding='utf-8')
        definition = '\n# 小物 @notebook\n* 名前: 手帳\n* 特徴: 赤い布表紙の手帳。\n* 画像: prop_sheet\n* 寸法: 幅148mm、高さ210mm、厚さ10mm。\n* 画像内位置: 左上の赤い手帳。\n'
        text = text.replace('# シナリオ', definition + '\n# シナリオ').replace('少女が駅を出て町を見渡す。', '@heroが@notebookを開く。')
        scenario = parse(text)
        self.assertEqual(scenario['beats'][0]['prop_ids'], ['notebook'])
        self.assertEqual(scenario['props'][0]['reference_hint'], '左上の赤い手帳。')
        doc = prop_document()
        self.assertEqual(parse(render_scene(doc)), doc)

    def test_two_props_share_one_native_picture_and_keep_separate_subjects(self):
        with temporary_directory() as directory:
            Path(directory, 'sheet.png').write_bytes(b'hashed shared reference; not decoded in this test')
            shared = registry([dict(id='prop_sheet', kind='image', locator='sheet.png')], directory)
            doc = prop_document()
            result = compile_fixture(doc, shared['assets'])
            segment = result.execution['segments'][0]
            self.assertEqual(segment['reference_bindings'], [dict(asset_id='prop_sheet', role='prop', token='<Picture 1>')])
            self.assertEqual(segment['local_props'], [dict(prop_id='notebook', subject_number=2), dict(prop_id='pencil', subject_number=3)])
            prompt = result.plan['shots'][0]['prompt']
            self.assertIn('<Subject 1> picks up <Subject 2> and <Subject 3>.', prompt)
            self.assertIn('148 mm wide, 210 mm tall, 10 mm thick.', prompt)
            self.assertIn('upper left', prompt)
            self.assertIn('does not set placement in the target Scene', prompt)
            self.assertEqual(prompt.count('Appearance reference: <Picture 1>.'), 2)
            with patch('vc_director.core.h3_contract.runtime.verify_runtime'):
                active = conditioning_inputs(result, shared, 1, prompt, result.plan['shots'][0]['length'], 'unused')
            self.assertEqual([a['id'] for a in active], ['prop_sheet'])
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).execution, result.execution)
            self.assertEqual(result.document['beats'][0]['dialogues'], doc['beats'][0]['dialogues'])

    def test_unused_props_do_not_add_references_or_definitions(self):
        doc = prop_document()
        scene = doc['scenes'][0]
        scene['visible_prop_ids'] = []
        scene['summary'] = 'The traveler waits.'
        next(e for sh in scene['shots'] for e in sh['events'] if e['kind']=='action')['text'] = scene['summary']
        result = compile_fixture(doc)
        self.assertEqual(result.execution['segments'][0]['reference_bindings'], [])
        self.assertNotIn('local_props', result.execution['segments'][0])
        self.assertNotIn('cloth-covered', result.plan['shots'][0]['prompt'])

    def test_ambiguous_ids_unknown_props_and_prop_speakers_are_rejected(self):
        doc = prop_document()
        doc['props'][0]['id'] = 'hero'
        with self.assertRaisesRegex(VCError, 'entity_ids'):
            validate_story(doc)
        doc = prop_document()
        doc['scenes'][0]['visible_prop_ids'] = ['unknown']
        with self.assertRaisesRegex(VCError, 'VISIBLE_PROP'):
            validate_story(doc)
        doc = prop_document()
        doc['beats'][0]['dialogues'][0]['actor_id'] = 'notebook'
        with self.assertRaisesRegex(VCError, 'DIALOGUE_PERMISSION'):
            validate_story(doc)

    def test_prop_only_scene_uses_object_owner_without_a_speaker(self):
        doc = prop_document()
        doc['actors'] = []
        beat, scene = doc['beats'][0], doc['scenes'][0]
        beat.update(actor_ids=[], dialogues=[], text='@notebook rests beside @pencil.')
        doc['audio']['vocal'] = beat['audio']['vocal'] = scene['audio']['vocal'] = 'none'
        scene.update(actor_ids=[], visible_actor_ids=[], subject_descriptions=[], retention=[],
                     summary=beat['text'], initial_state=[dict(owner_id='notebook', key='position', value='On the table.')], terminal_state=[])
        scene['shots'][0]['events'] = [dict(scene['shots'][0]['events'][0], actor_id='notebook', text='Static close-up of @notebook beside @pencil.')]
        for prop in doc['props']:
            prop.update(image_asset_id=None, reference_hint=None)
        result = compile_fixture(doc)
        self.assertEqual(result.execution['segments'][0]['local_subjects'], [])
        self.assertNotIn('speaks', result.plan['shots'][0]['prompt'])
        self.assertEqual(result.execution['segments'][0]['local_props'][0]['subject_number'], 1)

    def test_more_than_four_subject_markers_are_protected_when_props_are_added(self):
        doc = prop_document()
        for index in range(3):
            prop = dict(doc['props'][0], id=f'object_{index}', name=f'Object {index}', image_asset_id=None, reference_hint=None)
            doc['props'].append(prop)
            doc['beats'][0]['prop_ids'].append(prop['id'])
            doc['scenes'][0]['prop_ids'].append(prop['id'])
            doc['scenes'][0]['visible_prop_ids'].append(prop['id'])
        for prop in doc['props']:
            prop.update(image_asset_id=None, reference_hint=None)
        result = compile_fixture(doc)
        self.assertIn('<Subject 6>', result.plan['shots'][0]['prompt'])

    def test_generation_separates_prop_visibility_and_prop_actions_from_authored_speakers(self):
        doc = prop_document()
        scene = doc['scenes'][0]
        keys = ('id','beat_id','start_ms','end_ms','actor_ids','background_id','transition','depends_on','locked','required_event_ids','initial_state','terminal_state','prop_ids')
        payload = dict(scenario=doc, outline={k:scene[k] for k in keys}, previous_scenes=[])
        narrowed = generation_schema('detail', payload)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(scene))
        missing = copy.deepcopy(scene)
        del missing['prop_ids']
        self.assertFalse(validator.is_valid(missing))
        changed = copy.deepcopy(scene)
        next(e for sh in changed['shots'] for e in sh['events'] if e['kind']=='action')['actor_id'] = 'notebook'
        self.assertTrue(validator.is_valid(changed))
        next(e for sh in changed['shots'] for e in sh['events'] if e['kind']=='dialogue')['actor_id'] = 'notebook'
        self.assertFalse(validator.is_valid(changed))
        if importlib.util.find_spec('llama_cpp'):
            from llama_cpp import LlamaGrammar
            LlamaGrammar.from_string(record_grammar('DETAIL', payload_schema=narrowed), verbose=False)


if __name__ == '__main__':
    unittest.main()
