"""Neutral source-line and native integer-bound regression tests."""
import copy
import json
import random
import re
import unittest

from jsonschema import Draft202012Validator
from test_enhance_recovery import neutral_case, unfinished, NeutralBackend
from vc_director.core.direction import StoryEnhancer
from vc_director.core.emd import parse
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.integer_grammar import bounded_integer
from vc_director.core.protocols.llm import generation_schema


def source_case():
    text, scenario, expected, parts = neutral_case()
    offset = 88 - scenario["beats"][0]["source"]["first_line"]
    text = "\n" * offset + text
    scenario = parse(text)
    expected["scenario_sha256"] = scenario["source_sha256"]
    expected["constraints"] = copy.deepcopy(scenario["constraints"])
    for beat, authored in zip(expected["beats"], scenario["beats"]):
        beat["required_events"][0]["source"] = copy.deepcopy(authored["source"])
    parts = [dict(copy.deepcopy(expected), beats=[copy.deepcopy(beat)]) for beat in expected["beats"]]
    return text, scenario, expected, parts


def expression_regex(expression):
    """Translate the emitted literal/digit-class GBNF subset independently."""
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|\[[^\]]*\]|\{[0-9]+(?:,[0-9]+)?\}|[()|]')
    result, offset = [], 0
    for token in pattern.finditer(expression):
        if expression[offset:token.start()].strip():
            raise AssertionError("unexpected grammar token")
        value = token[0]
        result.append(re.escape(json.loads(value)) if value.startswith('"') else value)
        offset = token.end()
    if expression[offset:].strip():
        raise AssertionError("unexpected grammar suffix")
    return re.compile("".join(result))


class EnhanceSourceTests(unittest.TestCase):
    def test_native_integer_rule_enforces_bounds_instead_of_generic_integer(self):
        grammar = record_grammar("ENHANCED", payload_schema=dict(type="integer", minimum=88, maximum=97))
        line = next(line for line in grammar.splitlines() if line.startswith("vcd-payload-root ::="))
        self.assertNotIn("integer ::=", grammar)
        body = line.split(" ::= ", 1)[1].removesuffix(r" [ \t]{0,8}")
        matcher = expression_regex(body)
        for value in range(1, 200):
            self.assertEqual(matcher.fullmatch(str(value)) is not None, 88 <= value <= 97, value)

    def test_compact_integer_ranges_match_independent_numeric_comparisons(self):
        generator = random.Random(26)
        ranges = [(0, 0), (9, 10), (88, 97), (0, 15000), (-100, -12), (-9, 9), (999, 1001)]
        ranges += [tuple(sorted((generator.randint(-100000, 100000), generator.randint(-100000, 100000))))
                   for _ in range(150)]
        for first, last in ranges:
            matcher = expression_regex(bounded_integer(first, last))
            values = {first - 1, first, first + 1, last - 1, last, last + 1, 0}
            values.update(generator.randint(-100001, 100001) for _ in range(30))
            for value in values:
                self.assertEqual(matcher.fullmatch(str(value)) is not None, first <= value <= last,
                                 (first, last, value))
        self.assertLess(len(bounded_integer(-(2 ** 63), 2 ** 63 - 1)), 10000)

    def test_bounded_native_integer_does_not_allow_noncanonical_or_noninteger_text(self):
        matcher = expression_regex(bounded_integer(-1000, 1000))
        for value in ("088", "+88", "88.0", "8.8e1", "true", "nan", "--5", "-0", "88x", ""):
            self.assertIsNone(matcher.fullmatch(value), value)
        for bounds in ((9, 1), (True, 3), (1.5, 3)):
            with self.assertRaises(ValueError):
                bounded_integer(*bounds)

    def test_integer_range_does_not_override_constant_or_enum_constraints(self):
        for restriction in (dict(const=7), dict(enum=[7, 11])):
            schema = dict(type="integer", minimum=0, maximum=100, **restriction)
            grammar = record_grammar("ENHANCED", payload_schema=schema)
            self.assertNotIn("[0-9]", grammar)
            self.assertIn('"7"', grammar)
            if "enum" in restriction:
                self.assertIn('"11"', grammar)

    def test_generation_schema_requires_global_ordered_source_lines(self):
        _, scenario, _, parts = source_case()
        beat = scenario["beats"][0]
        self.assertEqual(beat["source"]["first_line"], 88)
        payload = dict(scenario=scenario, image_facts=[], enhance_beat_id=beat["id"])
        validator = Draft202012Validator(generation_schema("enhance", payload))
        self.assertTrue(validator.is_valid(parts[0]))
        first, last = beat["source"]["first_line"], beat["source"]["last_line"]
        for begin, end in ((1, 1), (first - 1, first), (first, last + 1), (last, first)):
            wrong = copy.deepcopy(parts[0])
            wrong["beats"][0]["required_events"][0]["source"].update(first_line=begin, last_line=end)
            self.assertFalse(validator.is_valid(wrong), (begin, end))
        prompt = system_prompt("enhance", payload)
        self.assertIn("入力EMD全体の88", prompt)
        self.assertIn("1行目へ振り直しません", prompt)
        grammar = record_grammar("ENHANCED", payload_schema=generation_schema("enhance", payload))
        self.assertIn("last-line", grammar)

    def test_precise_event_subrange_is_preserved_without_replacing_with_entire_beat(self):
        _, scenario, expected, _ = source_case()
        span = expected["beats"][0]["required_events"][0]["source"]
        span.update(first_line=89, last_line=89)
        original = copy.deepcopy(expected)
        result = StoryEnhancer(StageRunner(NeutralBackend([expected]))).enhance(scenario)
        self.assertEqual(result, original)
        self.assertEqual(result["beats"][0]["required_events"][0]["source"]["first_line"], 89)

    def test_invalid_sources_are_rejected_with_specific_japanese_expected_and_actual_values(self):
        text, scenario, expected, parts = source_case()
        authored = scenario["beats"][0]
        first, last = authored["source"]["first_line"], authored["source"]["last_line"]
        for mode in ("local_lines", "reverse", "foreign_beat_lines", "wrong_beat_id", "wrong_document"):
            wrong = copy.deepcopy(parts[0])
            event = wrong["beats"][0]["required_events"][0]
            if mode == "local_lines":
                event["source"].update(first_line=1, last_line=1)
                detail = "実際=1〜1行"
            elif mode == "reverse":
                event["source"].update(first_line=last, last_line=first)
                detail = "開始行と終了行が逆転"
            elif mode == "foreign_beat_lines":
                event["source"] = copy.deepcopy(scenario["beats"][1]["source"])
                detail = f"許容={first}〜{last}行"
            elif mode == "wrong_beat_id":
                event["beat_id"] = "departure"
                detail = "展開ID: 必要=arrival、実際=departure"
            else:
                event["source"]["document"] = "scene"
                detail = "文書種別: 必要=scenario、実際=scene"
            original = copy.deepcopy(wrong)
            backend = NeutralBackend([unfinished(expected), wrong, wrong, wrong])
            with self.subTest(mode=mode):
                with self.assertRaises(VCError) as caught:
                    StoryEnhancer(StageRunner(backend)).enhance(scenario)
                error = contextual_error(caught.exception, text)
                self.assertIn("enhanced_event_source", str(error))
                self.assertIn(detail, str(error))
                self.assertIn("対象展開 arrival", str(error))
                self.assertEqual(error.diagnostic.line, 88)
                self.assertIn("88行 | ## 展開 arrival", str(error))
                self.assertEqual(wrong, original)

    def test_valid_split_response_keeps_all_source_records(self):
        _, scenario, expected, parts = source_case()
        backend = NeutralBackend([unfinished(expected), *parts])
        result = StoryEnhancer(StageRunner(backend)).enhance(scenario)
        self.assertEqual(result, expected)
        self.assertEqual([event["source"] for beat in result["beats"] for event in beat["required_events"]],
                         [beat["source"] for beat in scenario["beats"]])
        self.assertEqual(len(backend.requests), 3)


if __name__ == "__main__":
    unittest.main()
