import copy
import json
import unittest

from test_core import FakeBackend, english_document, compile_fixture, temporary_directory, with_camera
from test_prop_tags import prop_document
from vc_director.core.artifacts.validation import validate_story, validate_visual_directions, _prose
from vc_director.core.artifacts.actor_references import ActorReferences
from vc_director.core.artifacts.storage import save, load
from vc_director.core.compiler.whole_field import MaskedField
from vc_director.core.compiler.protection import ProtectedUnit
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.protocols.visual_repair import repair_visual_directions, is_visual_direction_error
from vc_director.core.protocols import StageRunner
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.artifacts.schema import schema
from test_core import fixture


class VisibleTextTests(unittest.TestCase):
    def test_visual_stray_dialogue_repairs_only_the_field_and_retains_authored_visible_words(self):
        doc = prop_document()
        scene = doc['scenes'][0]
        doc['beats'][0]['text'] += ' A screen displays "予想を更新しました".'
        scene['visual_directions'] = ['@notebookに「予想を更新しました」と表示される文字を映す。']
        error = Diagnostic('STRAY_DIALOGUE', scene['id'] + '/visual_directions/0', 'Separate visible text from speech')
        self.assertTrue(is_visual_direction_error(error, scene))
        backend = FakeBackend(['DETAIL\t1\t' + json.dumps(dict(visual_directions=[
            '@notebookに<visible>予想を更新しました</visible>と表示される文字を映す。']),ensure_ascii=False)])
        original = copy.deepcopy(scene)
        fixed = repair_visual_directions(backend, scene, dict(scenario=doc), error, {})
        validate_visual_directions(fixed)
        self.assertEqual(scene, original)
        self.assertEqual({k:v for k,v in fixed.items() if k!='visual_directions'},
                         {k:v for k,v in scene.items() if k!='visual_directions'})
        self.assertEqual(backend.requests[0]['authored_beat'], doc['beats'][0])
        self.assertIn('予想を更新しました', fixed['visual_directions'][0])

    def test_repair_receives_feedback_when_it_returns_speech_quotation_again(self):
        doc = prop_document();scene=doc['scenes'][0]
        scene['visual_directions']=['A screen displays 「通知」.']
        backend=FakeBackend(['DETAIL\t1\t'+json.dumps(dict(visual_directions=[s]),ensure_ascii=False)
                             for s in ('A screen displays 「通知」.','A screen displays <visible>通知</visible>.')])
        fixed=repair_visual_directions(backend,scene,dict(scenario=doc),
            Diagnostic('STRAY_DIALOGUE',scene['id']+'/visual_directions/0','Speech quotation'),{})
        self.assertEqual(backend.requests[1]['validation_error']['code'],'STRAY_DIALOGUE')
        self.assertIn('<visible>通知</visible>',fixed['visual_directions'][0])

    def test_compilation_and_revision_replay_keep_japanese_visible_words_without_speech(self):
        doc=english_document();scene=with_camera(doc['scenes'][0])
        scene.update(visible_actor_ids=['hero'],visual_directions=[
            '@hero sees <visible>今夜、流星群の観測好機</visible> and <visible>予想を更新しました</visible> on the screen.'])
        doc['scenes']=[scene]
        result=compile_fixture(doc)
        prompt=result.plan['shots'][0]['prompt']
        self.assertIn('『今夜、流星群の観測好機』',prompt)
        self.assertIn('『予想を更新しました』',prompt)
        self.assertNotIn('<visible>',prompt)
        self.assertEqual(prompt.count('<d>'),1)
        self.assertEqual(result.document['beats'][0]['dialogues'],doc['beats'][0]['dialogues'])
        with temporary_directory() as directory:
            revision=save(result,directory)
            self.assertEqual(load(revision.parents[1]).plan,result.plan)

    def test_visible_username_and_vocal_words_do_not_create_actor_or_speech_instructions(self):
        doc=english_document();scene=with_camera(doc['scenes'][0])
        literal='<visible>@not_a_character says their name</visible>'
        scene.update(visible_actor_ids=['hero'],visual_directions=['A screen displays '+literal+'.'])
        doc['scenes']=[scene]
        result=compile_fixture(doc)
        self.assertEqual(len(result.execution['segments'][0]['local_subjects']),1)
        self.assertIn('『@not_a_character says their name』',result.plan['shots'][0]['prompt'])
        self.assertEqual(ActorReferences(doc['actors']).bind('<visible>hero</visible>','test'),'<visible>hero</visible>')

    def test_malformed_visible_markup_and_speech_tags_are_still_rejected(self):
        for text in ('<visible>','<visible></visible>','<visible>one\ntwo</visible>',
                     '<visible>「spoken words」</visible>','<visible><d>speech</d></visible>'):
            with self.assertRaises(VCError):_prose(text,'test',[],cues=True)
        with self.assertRaises(VCError):_prose('A person whispers.','test',[],cues=True)

    def test_visible_words_are_an_immutable_whole_field_span(self):
        literal='<visible>予想を更新しました</visible>'
        field=MaskedField.from_unit(ProtectedUnit(('Display ','.'),(literal,)))
        request=json.loads(field.payload())
        self.assertEqual(request['immutable_spans'][0]['kind'],'visible_text')
        self.assertEqual(field.restore('Display __MVD_KEEP_0001__.'),'Display '+literal+'.')

    def test_repaired_visible_words_survive_unrelated_sound_trigger_recovery(self):
        doc=prop_document();good=doc['scenes'][0]
        for audio in (doc['audio'],doc['beats'][0]['audio'],good['audio']):audio['effects']='expand'
        doc['beats'][0]['text']+=' The screen displays "通知".'
        good['visual_directions']=['The screen displays <visible>通知</visible> beside @notebook.']
        action=next(e for sh in good['shots'] for e in sh['events'] if e['kind']=='action')
        good['sound_events']=[dict(id='notification',kind='effect',start_ms=0,end_ms=1000,trigger_event_id=action['id'],text='A brief physical notification tone.')]
        bad=copy.deepcopy(good)
        bad['visual_directions']=['The screen displays 「通知」 beside @notebook.']
        bad['sound_events'][0]['trigger_event_id']='absent_event'
        retry=copy.deepcopy(good);retry['visual_directions']=bad['visual_directions'][:]
        scenario={k:copy.deepcopy(v) for k,v in doc.items() if k not in {'schema','scenario_sha256','scenes','required_events'}}
        scenario.update(schema='VCD_SCENARIO_V1',source_sha256=doc['scenario_sha256'])
        outline={k:copy.deepcopy(good[k]) for k in schema()['$defs']['scene_outline']['required']}
        outline['prop_ids']=good['prop_ids'][:]
        backend=FakeBackend(['SCENES\t1\t'+json.dumps(dict(scenes=[outline])),
            'DETAIL\t1\t'+json.dumps(bad,ensure_ascii=False),
            'DETAIL\t1\t'+json.dumps(dict(visual_directions=good['visual_directions']),ensure_ascii=False),
            'DETAIL\t1\t'+json.dumps(retry,ensure_ascii=False)])
        result=StoryTimelinePlanner(StageRunner(backend)).plan(scenario,fixture('enhanced-story'))
        self.assertEqual(result['scenes'][0],good)
        self.assertEqual(len(backend.requests),4)
        self.assertEqual(backend.requests[-1]['errors'][0]['code'],'sound_trigger')


if __name__=='__main__':unittest.main()
