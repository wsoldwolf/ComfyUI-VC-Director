import copy
import importlib.util
import unittest

from jsonschema import Draft202012Validator

from test_core import FakeBackend, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.planner.budget import allocate_beat_windows, budget_schema, validate_windows
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.utilities import canonical


def windows_for(scenario, cuts):
    return [dict(beat_id=beat['id'], start_ms=start, end_ms=end)
            for beat, start, end in zip(scenario['beats'], cuts, cuts[1:])]


class OutlineBudgetTests(unittest.TestCase):
    def test_exhausted_prefix_replans_before_requesting_zero_length_final_beat(self):
        scenario, enhanced, outlines, document = continued_story()
        # The invalid prefix is individually legal but has consumed the full
        # authored total before the last beat. No local retry can fix it.
        scenario['duration_ms'] = 20000
        windows = windows_for(scenario, (0, 10000, 20000))
        replanned = [outlines[0], dict(outlines[2], start_ms=10000, end_ms=20000,
                                    depends_on=[outlines[0]['id']])]
        responses = [
            'SCENES\t1\t' + canonical(dict(scenes=outlines[:2])),
            'SCENES\t1\t' + canonical(dict(windows=windows)),
            *['SCENES\t1\t' + canonical(dict(scenes=[o])) for o in replanned]]
        backend = FakeBackend(responses)
        runner = StageRunner(backend)
        original = copy.deepcopy((scenario, enhanced))
        result = StoryTimelinePlanner(runner)._plan_outlines(scenario, enhanced)
        self.assertEqual(result, replanned)
        self.assertEqual((scenario, enhanced), original)
        self.assertEqual(backend.requests[1]['task'], 'allocate_beat_windows')
        for request in backend.requests[2:]:
            self.assertEqual(request['payload']['beat_windows'], windows)
        # There was no doomed outline request for the final beat at 20000ms.
        self.assertEqual(backend.requests[3]['payload']['accepted_outlines'][-1]['end_ms'], 10000)
        details = [dict(copy.deepcopy(scene), **outline)
                   for scene, outline in zip((document['scenes'][0], document['scenes'][2]), replanned)]
        backend = FakeBackend(responses + ['DETAIL\t1\t' + canonical(scene) for scene in details])
        document = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(document['duration_ms'], 20000)
        self.assertEqual(len(document['scenes']), 2)
        self.assertEqual(document['beats'], scenario['beats'])
        self.assertEqual([e['id'] for e in document['required_events']],
                         [e['id'] for beat in enhanced['beats'] for e in beat['required_events']])

    def test_windows_reject_gaps_overlap_zero_reorder_drop_and_changed_total(self):
        scenario, enhanced, outlines, _ = continued_story()
        valid = dict(windows=windows_for(scenario, (0, 15000, 30000)))
        self.assertEqual(validate_windows(valid, scenario), valid['windows'])
        invalid = []
        for index, key, value in ((0, 'end_ms', 16000), (1, 'start_ms', 14000),
                                  (0, 'end_ms', 0), (1, 'end_ms', 31000)):
            changed = copy.deepcopy(valid)
            changed['windows'][index][key] = value
            invalid.append(changed)
        invalid.extend([dict(windows=valid['windows'][::-1]), dict(windows=valid['windows'][:1])])
        for value in invalid:
            with self.assertRaises(VCError):
                validate_windows(value, scenario)
        with self.assertRaises(VCError):
            budget_schema(dict(scenario, duration_ms=1))

    def test_invalid_budget_has_two_bounded_attempts_and_success_only_cache(self):
        scenario, enhanced, outlines, _ = continued_story()
        windows = windows_for(scenario, (0, 15000, 30000))
        bad = dict(windows=copy.deepcopy(windows))
        bad['windows'][1]['start_ms'] = 14000
        with temporary_directory() as directory:
            backend = FakeBackend(['SCENES\t1\t' + canonical(bad)] * 2)
            runner = StageRunner(backend, directory)
            with self.assertRaisesRegex(VCError, 'OUTLINE_BUDGET_RETRIES_EXHAUSTED'):
                allocate_beat_windows(runner, scenario, enhanced, outlines)
            self.assertEqual(len(backend.requests), 2)
            self.assertIn('validation_error', backend.requests[1])
            backend = FakeBackend(['SCENES\t1\t' + canonical(dict(windows=windows))])
            runner = StageRunner(backend, directory)
            self.assertEqual(allocate_beat_windows(runner, scenario, enhanced, outlines), windows)
            self.assertEqual(allocate_beat_windows(runner, scenario, enhanced, outlines), windows)
            self.assertEqual(len(backend.requests), 1)

    def test_allocated_windows_bind_grammar_including_long_beats(self):
        scenario, enhanced, outlines, _ = continued_story()
        for duration, count in ((15000, 1), (30000, 2)):
            scenario['duration_ms'] = duration + 10000
            windows = windows_for(scenario, (0, duration, duration + 10000))
            payload = dict(scenario=scenario, enhanced=enhanced, accepted_outlines=[], beat_windows=windows)
            narrowed = generation_schema('outline', payload)
            scenes = [dict(outlines[0], id=f'scene_{i}', start_ms=i * 15000,
                           end_ms=(i + 1) * 15000, transition='cut' if i == 0 else 'continue') for i in range(count)]
            validator = Draft202012Validator(narrowed)
            self.assertTrue(validator.is_valid(dict(scenes=scenes)))
            changed = copy.deepcopy(scenes)
            changed[-1]['end_ms'] += 1
            self.assertFalse(validator.is_valid(dict(scenes=changed)))
            self.assertFalse(validator.is_valid(dict(scenes=scenes * 2)))
            self.assertIn(f'0〜{duration}ms', system_prompt('outline', payload))
            if importlib.util.find_spec('llama_cpp'):
                from llama_cpp import LlamaGrammar
                LlamaGrammar.from_string(record_grammar('SCENES', payload_schema=narrowed), verbose=False)
        if importlib.util.find_spec('llama_cpp'):
            from llama_cpp import LlamaGrammar
            LlamaGrammar.from_string(record_grammar('SCENES', payload_schema=budget_schema(scenario)), verbose=False)

    def test_unspent_or_unspecified_total_does_not_add_budget_call(self):
        for total in (30000, None):
            scenario, enhanced, outlines, _ = continued_story()
            scenario['duration_ms'] = total
            backend = FakeBackend(['SCENES\t1\t' + canonical(dict(scenes=items))
                                   for items in (outlines[:2], outlines[2:])])
            result = StoryTimelinePlanner(StageRunner(backend))._plan_outlines(scenario, enhanced)
            self.assertEqual(result, outlines)
            self.assertTrue(all('task' not in request for request in backend.requests))


if __name__ == '__main__':
    unittest.main()
