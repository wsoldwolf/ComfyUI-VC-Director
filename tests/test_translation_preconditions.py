"""Prevent repeated first-attempt failures using only neutral in-memory prose."""
import json
import re
import unittest

from test_translation_alias_repair import NeutralBackend, record, generated_rules_accept
from vc_director.core.artifacts.actor_references import ActorReferences, ENGLISH_PERSON_PRONOUNS
from vc_director.core.compiler.translation_slots import translation_grammar
from vc_director.core.inference.translator import GemmaTranslator, BOUND_OWNER_INSTRUCTIONS
from vc_director.core.errors import VCError


ACTORS = [dict(id="man", name="旅人"), dict(id="guide", name="案内人")]
ROLES = {"<Subject 1>": "actor", "<Subject 2>": "actor", "<Subject 3>": "prop", "<Subject 4>": "prop"}


def slot_record(after):
    value = dict(prefix="", slots=[dict(slot=i, position=i, after=text) for i, text in enumerate(after, 1)])
    return "TRANSLATION\t1\t" + json.dumps(value)


def context(backend, index=0):
    return json.loads(backend.requests[index][1]["content"].removeprefix("/no_think\n"))["field_context"]


def text_rules(grammar):
    return [line for line in grammar.splitlines() if re.match(r"text-[0-9]+ ::=", line)]


class TranslationPreconditionsTests(unittest.TestCase):
    def test_common_word_id_is_constrained_on_first_definition_without_warning(self):
        backend = NeutralBackend([record("A male adult in a blue coat.")])
        with self.assertNoLogs("mv_director.compiler", level="WARNING"):
            result = GemmaTranslator(backend).translate_actor_protected(("青い上着を着た成人男性。",), (), ACTORS,
                owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertEqual(result, "A male adult in a blue coat.")
        self.assertEqual(len(backend.requests), 1)
        self.assertIn("man", context(backend)["forbidden_person_aliases"])
        rules = text_rules(backend.grammars[0])
        self.assertFalse(generated_rules_accept(rules, "A man in a blue coat."))
        self.assertTrue(generated_rules_accept(rules, result))

    def test_unbound_pronouns_are_blocked_on_first_multi_actor_translation(self):
        backend = NeutralBackend([slot_record([" watches ", " approach the gate."])])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が", "を見つめる。"),
            ("<Subject 1>", "<Subject 2>"), ACTORS, explicit_people=True, allow_bound_pronouns=True,
            subject_roles=ROLES)
        self.assertEqual(result, "<Subject 1> watches <Subject 2> approach the gate.")
        self.assertIsNone(context(backend)["pronoun_owner"])
        self.assertEqual(context(backend)["forbidden_person_pronouns"], list(ENGLISH_PERSON_PRONOUNS))
        rules = text_rules(backend.grammars[0])
        for word in ENGLISH_PERSON_PRONOUNS:
            self.assertFalse(generated_rules_accept(rules, f"move {word.upper()} toward the gate"), word)
        for text in ("the gate", "theory", "theme", "mother", "therefore", "a human figure"):
            self.assertTrue(generated_rules_accept(rules, text), text)

    def test_uniquely_bound_actor_with_multiple_props_retains_their(self):
        backend = NeutralBackend([slot_record([" holds ", " beside ", " in their hands."])])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が", "と", "を持つ。"),
            ("<Subject 1>", "<Subject 3>", "<Subject 4>"), ACTORS, explicit_people=True,
            owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertEqual(context(backend)["pronoun_owner"], "<Subject 1>")
        self.assertEqual(context(backend)["forbidden_person_pronouns"], [])
        self.assertIn("their hands", result)
        self.assertTrue(generated_rules_accept(text_rules(backend.grammars[0]), " in their hands."))

    def test_single_explicit_actor_can_bind_pronouns_without_structural_owner(self):
        backend = NeutralBackend([slot_record([" lowers their gaze."])])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が視線を下げる。"),
            ("<Subject 1>",), ACTORS, explicit_people=True, allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertEqual(result, "<Subject 1> lowers their gaze.")
        self.assertEqual(context(backend)["pronoun_owner"], "<Subject 1>")
        self.assertEqual(context(backend)["forbidden_person_pronouns"], [])

    def test_marker_free_unbound_field_does_not_create_an_identity(self):
        backend = NeutralBackend([record("Two figures wait by the station entrance.")])
        result = GemmaTranslator(backend).translate_actor_protected(("二人が駅の入口で待つ。",), (), ACTORS,
            explicit_people=True, allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertNotIn("<Subject", result)
        request = json.loads(backend.requests[0][1]["content"].removeprefix("/no_think\n"))
        self.assertEqual(request["immutable_spans"], [])
        self.assertFalse(generated_rules_accept(text_rules(backend.grammars[0]), "They wait by the station."))

    def test_legacy_strict_pronoun_mode_keeps_its_source_constraints(self):
        backend = NeutralBackend([slot_record([" turns toward the gate."])])
        GemmaTranslator(backend).translate_actor_protected(("", "が門へ向き直る。"),
            ("<Subject 1>",), ACTORS, explicit_people=True)
        self.assertEqual(context(backend)["forbidden_person_pronouns"], list(ENGLISH_PERSON_PRONOUNS))

    def test_property_owner_is_not_used_as_personal_antecedent(self):
        backend = NeutralBackend([slot_record([" rests beside the gate."])])
        GemmaTranslator(backend).translate_actor_protected(("", "が門の脇に置かれている。"),
            ("<Subject 3>",), ACTORS, explicit_people=True, owner_subject="<Subject 3>",
            allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertIsNone(context(backend)["pronoun_owner"])
        self.assertFalse(generated_rules_accept(text_rules(backend.grammars[0]), "their corner"))

    def test_context_and_constraints_are_determined_again_for_each_field(self):
        backend = NeutralBackend([slot_record([" lowers their gaze."]), record("Two figures wait by the entrance.")])
        translator = GemmaTranslator(backend)
        translator.translate_actor_protected(("", "が視線を下げる。"), ("<Subject 1>",), ACTORS,
            explicit_people=True, owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=ROLES)
        translator.translate_actor_protected(("二人が入口で待つ。",), (), ACTORS,
            explicit_people=True, allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertEqual(context(backend, 0)["forbidden_person_pronouns"], [])
        self.assertEqual(context(backend, 1)["forbidden_person_pronouns"], list(ENGLISH_PERSON_PRONOUNS))
        self.assertNotEqual(backend.grammars[0], backend.grammars[1])

    def test_owner_instructions_are_present_also_for_single_actor_definitions(self):
        backend = NeutralBackend([record("An adult in a blue coat.")])
        GemmaTranslator(backend).translate_actor_protected(("青い上着の成人。",), (), ACTORS,
            explicit_people=False, owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertIn(BOUND_OWNER_INSTRUCTIONS, backend.requests[0][0]["content"])

    def test_backend_ignoring_grammar_is_still_rejected_after_two_attempts(self):
        backend = NeutralBackend([record("A man in a blue coat."), record("They wait by the gate.")])
        with self.assertRaisesRegex(VCError, "TRANSLATION_RETRIES_EXHAUSTED"):
            GemmaTranslator(backend).translate_actor_protected(("駅の入口で待つ。",), (), ACTORS,
                explicit_people=True, allow_bound_pronouns=True, subject_roles=ROLES)
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(backend.grammars[0], backend.grammars[1])

    def test_plain_translator_without_actor_context_keeps_its_grammar(self):
        backend = NeutralBackend([record("A bright station.")])
        self.assertEqual(GemmaTranslator(backend)("明るい駅。"), "A bright station.")
        self.assertEqual(backend.grammars, [translation_grammar(0)])


if __name__ == "__main__":
    unittest.main()
