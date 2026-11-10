import copy
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FakeBackend, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.inference import GemmaBackend, LlamaRuntimeConfig
from vc_director.core.inference.llama_cpp_backend import LlamaCppLifecycle, release_comfy_gpu_models
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.protocols.context import detail_request
from vc_director.core.utilities import canonical


class LanguagePerformanceTests(unittest.TestCase):
    def test_detail_scope_preserves_current_source_and_global_identity_and_dependencies(self):
        scenario, enhanced, outlines, document = continued_story()
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outlines[2],
                       previous_scenes=document['scenes'][:2], locked_scene=None)
        request = dict(payload=payload, errors=[dict(code='shot_coverage')], retry='repair')
        original = copy.deepcopy(request)
        actual = detail_request(request)
        self.assertEqual(request, original)
        self.assertEqual(actual['payload']['scenario']['beats'], scenario['beats'][1:])
        self.assertEqual(actual['payload']['enhanced']['beats'], enhanced['beats'][1:])
        for key in scenario.keys() - {'beats'}:
            self.assertEqual(actual['payload']['scenario'][key], scenario[key])
        for key in ('outline', 'previous_scenes', 'locked_scene'):
            self.assertEqual(actual['payload'][key], payload[key])
        self.assertEqual(actual['errors'], request['errors'])

    def test_native_runner_logs_full_source_and_scoped_request_and_performance(self):
        scenario, enhanced, outlines, document = continued_story()
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outlines[0],
                       previous_scenes=[], locked_scene=None)
        scene = document['scenes'][0]
        class Native(FakeBackend):
            trace = []
            def complete_record(self, system, request, record_type, *, payload_schema):
                self.trace = [dict(performance=dict(completion_seconds=1.5))]
                self.system, self.grammar_schema = system, payload_schema
                return self.complete(system, request)
        backend = Native(['DETAIL\t1\t' + canonical(scene)])
        with temporary_directory() as directory:
            actual = StageRunner(backend, diagnostics_root=directory).run('detail', scene['id'], payload,
                                                                          lambda result: self.assertEqual(result, scene))
            self.assertEqual(actual, scene)
            self.assertEqual(len(backend.requests[0]['payload']['scenario']['beats']), 1)
            self.assertLess(len(backend.system), len(system_prompt('detail', payload)))
            self.assertIn('$defs', backend.grammar_schema)
            diagnostic = json.loads(next(Path(directory).glob('detail-*.json')).read_text(encoding='utf-8'))
            self.assertEqual(diagnostic['request']['payload'], payload)
            self.assertEqual(diagnostic['attempts'][0]['inference_request'], backend.requests[0])
            self.assertEqual(diagnostic['attempts'][0]['performance']['completion_seconds'], 1.5)

    def test_gpu_handoff_runs_only_before_new_gpu_load_in_current_process(self):
        events = []
        device = types.SimpleNamespace(type='cuda')
        manager = types.SimpleNamespace(get_torch_device=lambda: device,
            free_memory=lambda size, target: events.append(('free', target)),
            soft_empty_cache=lambda: events.append(('empty',)))
        class Model:
            def __init__(self, **kwargs): events.append(('load',))
            def close(self): events.append(('close',))
        module = types.SimpleNamespace(GGML_TYPE_Q8_0=8)
        with temporary_directory() as directory, patch.dict(sys.modules, {'comfy.model_management': manager}):
            path = Path(directory, 'model.gguf')
            path.write_bytes(b'mocked model')
            life = LlamaCppLifecycle(llama_module=module, llama_class=Model)
            life.ensure_loaded(path, LlamaRuntimeConfig())
            life.ensure_loaded(path, LlamaRuntimeConfig())
            self.assertEqual([e[0] for e in events], ['free', 'empty', 'load'])
            events.clear()
            release_comfy_gpu_models(LlamaRuntimeConfig(gpu_layers=0))
            self.assertEqual(events, [])
            life.clear()

    def test_completion_metrics_observe_first_text_and_stream_without_changing_response(self):
        metrics = {}
        stream = [dict(choices=[dict(delta=dict(content=text))]) for text in ('', 'DETAIL\t1\t', '{}')]
        self.assertEqual(LlamaCppLifecycle._collect_chat_stream(stream, None, metrics), 'DETAIL\t1\t{}')
        self.assertGreaterEqual(metrics['first_text_seconds'], 0)
        self.assertGreaterEqual(metrics['stream_seconds'], metrics['first_text_seconds'])

    def test_backend_records_load_and_completion_metrics_without_an_extra_model_load(self):
        with temporary_directory() as directory:
            path = Path(directory, 'model.gguf')
            path.write_bytes(b'mocked model')
            backend = GemmaBackend(path)
            calls = []
            backend.lifecycle = types.SimpleNamespace(ensure_loaded=lambda *args: calls.append('load'),
                count_serialized_prompt=lambda text: types.SimpleNamespace(count=len(text), estimated=False),
                effective_n_ctx=24576, complete_chat=lambda *args, **kwargs: 'done', clear=lambda: None,
                last_completion_metrics=dict(first_text_seconds=0.5, stream_seconds=1.0))
            self.assertEqual(backend.complete('system', 'request'), 'done')
            self.assertEqual(calls, ['load'])
            performance = backend.trace[-1]['performance']
            self.assertEqual(performance['output_tokens'], 4)
            self.assertEqual(performance['first_text_seconds'], 0.5)
            self.assertGreaterEqual(performance['total_seconds'], performance['completion_seconds'])


if __name__ == '__main__':
    unittest.main()
