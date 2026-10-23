import importlib.util
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import temporary_directory
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaBackend, GemmaTranslator, LlamaRuntimeConfig
from vc_director.core.inference import ContextBudgetError


class InferenceTests(unittest.TestCase):
    def test_budget_failure_clears_model_without_chat(self):
        with temporary_directory() as temp:
            path = Path(temp, "test.gguf")
            path.write_bytes(b"fake GGUF; lifecycle injected")
            backend = GemmaBackend(path)
            state = dict(clears=0, chats=0)
            backend.lifecycle = types.SimpleNamespace(
                ensure_loaded=lambda *args: None,
                count_serialized_prompt=lambda value: types.SimpleNamespace(count=30000, estimated=False),
                effective_n_ctx=24576,
                clear=lambda: state.update(clears=state["clears"] + 1),
                complete_chat=lambda *args, **kwargs: state.update(chats=state["chats"] + 1),
            )
            with self.assertRaises(ContextBudgetError):
                backend.complete("system", "mandatory source")
            self.assertEqual(state, dict(clears=1, chats=0))

    def test_reused_mv_protected_translation_and_success_cache(self):
        with temporary_directory() as temp:
            path = Path(temp, "test.gguf")
            path.write_bytes(b"fake GGUF; lifecycle injected")
            backend = GemmaBackend(path, LlamaRuntimeConfig(n_ctx=16384, max_tokens=4096))
            state = dict(chats=0, clears=0)

            def complete(messages, config, **kwargs):
                self.assertNotIn("ただいま", str(messages))
                state["chats"] += 1
                return "TRANSLATION\t1\t__MVD_KEEP_0001__ walks."

            backend.lifecycle = types.SimpleNamespace(ensure_loaded=lambda *args: None, count_serialized_prompt=lambda value: types.SimpleNamespace(count=100, estimated=False),
                                                     effective_n_ctx=16384, clear=lambda: state.update(clears=state["clears"] + 1), complete_chat=complete)
            translator = GemmaTranslator(backend, Path(temp, "cache"))
            for _ in range(2):
                self.assertEqual(translator.translate_protected(("", "が歩く。"), ("<Subject 1>",)), "<Subject 1> walks.")
            self.assertEqual(state, dict(chats=1, clears=0))
            backend.close()
            self.assertEqual(state["clears"], 1)


MEDIA_AVAILABLE = all(importlib.util.find_spec(name) for name in ("torch", "av", "PIL", "numpy"))


@unittest.skipUnless(MEDIA_AVAILABLE, "Optional CPU media dependencies unavailable")
class MediaTests(unittest.TestCase):
    def test_video_12fps_is_resampled_to_24fps_and_audio_is_opt_in(self):
        import av
        import numpy as np
        from vc_director.core.vision.reference_video import decode_reference_video

        with temporary_directory() as temp:
            path = Path(temp, "reference.mp4")
            with av.open(str(path), "w") as container:
                stream = container.add_stream("mpeg4", rate=12)
                stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
                for i in range(6):
                    frame = av.VideoFrame.from_ndarray(np.full((32, 32, 3), i * 20, dtype=np.uint8), format="rgb24")
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            images, audio = decode_reference_video(str(path))
            self.assertEqual(tuple(images.shape), (12, 32, 32, 3))
            self.assertIsNone(audio)
            with self.assertRaisesRegex(VCError, "VIDEO_AUDIO"):
                decode_reference_video(str(path), paired_audio=True)

    def test_vision_reuses_visibility_and_preserves_input_image(self):
        import torch
        from vc_director.core.vision import image_to_entity
        from vc_director.core.artifacts.observations import ObservationsArtifact, SubjectFeature

        values = dict.fromkeys(("overview", "primary_subject", "hint_reason", "subject_pose", "scene_setting", "lighting", "time_weather", "shot_size", "viewpoint", "subject_placement", "depth", "style_medium", "style_rendering", "style_palette"), "")
        values.update(overview="A traveler in a square.", primary_subject="A traveler.", hint_status="not_used", scene_setting="A town square.", scene_elements=(), visible_text=(), uncertainties=(),
                      subject_features=(SubjectFeature("clothing", "A red coat.", "clear"), SubjectFeature("eyes", "Possibly blue eyes.", "uncertain")))
        observations = ObservationsArtifact(**values)
        image = torch.zeros((1, 32, 32, 3))
        fake_cache = types.SimpleNamespace(get=lambda key: dict(observations=observations.to_dict(), warnings=[]))
        pair = types.SimpleNamespace(signature=lambda: {"model": "fake fixture"})
        with patch("vc_director.core.vision.image_to_subject.SuccessCache", return_value=fake_cache):
            fragment, binding, passed, raw = image_to_entity(image, pair, "hero", "hero_image", "Traveler", cache_root="unused")
            self.assertIs(passed, image)
            self.assertIn("# 人物 @hero", fragment)
            self.assertIn("A red coat.", fragment)
            self.assertNotIn("Possibly blue eyes", fragment)
            self.assertEqual(binding["asset_id"], "hero_image")
            background, _, _, _ = image_to_entity(image, pair, "square", "square_image", "Square", cache_root="unused", analysis_profile="scene_only")
            self.assertIn("# 背景 square", background)
            self.assertIn("A town square.", background)
            self.assertNotIn("# 人物", background)


if __name__ == "__main__":
    unittest.main()
