"""Whole-field English with source-owned occurrences, never model-written markers."""
import json
from functools import lru_cache
from pathlib import Path

from .errors import CompilerError


def slot_prompt():
    return (Path(__file__).resolve().parents[2] / "prompts/prompt_translation_slots_system_prompt.txt").read_text(encoding="utf-8")


def _literal(text):
    return json.dumps(text, ensure_ascii=False)


def _text_rules(*, json_string=False):
    # DFA for the reserved prefix, including mixed case. Escaped Unicode is
    # intentionally unavailable: it must not bypass this text alphabet.
    forbidden = "__mvd_keep_"
    alphabet = sorted(set(forbidden + forbidden.upper()))
    rules = []
    for state in range(len(forbidden)):
        targets, blocked = {}, set()
        for char in alphabet:
            value = forbidden[:state] + char.lower()
            if value.endswith(forbidden):
                blocked.add(char)
                continue
            target = max(n for n in range(len(forbidden)) if value.endswith(forbidden[:n]))
            if target:
                targets.setdefault(target, []).append(char)
        # All characters returning to state zero share ONE character class.
        # Separate literal alternatives inflate llama.cpp's active grammar
        # stacks and repeat work for every token in its 262k-entry vocabulary.
        excluded_chars = blocked | {char for chars in targets.values() for char in chars} | {'<'}
        if json_string:
            excluded_chars |= {'"', '\\'}
        excluded = "".join(sorted(excluded_chars)).replace("\\", "\\\\").replace('"', '\\"')
        alternatives = ['""', f'[^\\x00-\\x1F{excluded}] text-0']
        if json_string:
            alternatives.append(r'("\\\"" | "\\\\" | "\\/") text-0')
        for target, chars in sorted(targets.items()):
            symbol = _literal(chars[0]) if len(chars) == 1 else '[' + ''.join(chars) + ']'
            alternatives.append(f"{symbol} text-{target}")
        rules.append(f"text-{state} ::= " + " | ".join(alternatives))
    return rules


@lru_cache(maxsize=128)
def translation_grammar(count=0, forbidden_aliases=()):
    header = _literal("TRANSLATION\t1\t")
    def text_rules(*, json_string=False):
        if forbidden_aliases:
            from .translation_lexicon import guarded_text_rules
            return guarded_text_rules(forbidden_aliases, json_string=json_string)
        return _text_rules(json_string=json_string)
    if not count:
        return "\n".join([f'root ::= {header} text-0 "\\n"?', *text_rules()]) + "\n"
    ranks = " | ".join(_literal(str(i)) for i in range(1, count + 1))
    entries = []
    for index in range(1, count + 1):
        entries.append(f'slot-{index} ::= ' + _literal('{"slot":' + str(index) + ',"position":')
                       + ' rank ' + _literal(',"after":') + ' string ' + _literal('}'))
    array = (' ' + _literal(',') + ' ').join(f'slot-{i}' for i in range(1, count + 1))
    return "\n".join([
        f'root ::= {header} ' + _literal('{"prefix":') + ' string ' + _literal(',"slots":[')
        + ' ' + array + ' ' + _literal(']}') + ' "\\n"?',
        'string ::= "\\\"" text-0 "\\\""', 'rank ::= ' + ranks,
        *entries, *text_rules(json_string=True),
    ]) + "\n"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompilerError(f"occurrence-slot duplicate key: {key}")
        result[key] = value
    return result


def restore_slots(candidate, markers):
    try:
        value = json.loads(candidate, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError) as error:
        raise CompilerError("occurrence-slot JSON protocol mismatch") from error
    if not isinstance(value, dict) or set(value) != {"prefix", "slots"} or not isinstance(value["prefix"], str):
        raise CompilerError("occurrence-slot JSON protocol mismatch")
    slots = value["slots"]
    if not isinstance(slots, list) or len(slots) != len(markers):
        raise CompilerError(f"occurrence-slot coverage mismatch: expected={len(markers)}, got={len(slots) if isinstance(slots, list) else 'not an array'}")
    for index, slot in enumerate(slots, 1):
        if (not isinstance(slot, dict) or set(slot) != {"slot", "position", "after"}
                or type(slot["slot"]) is not int or slot["slot"] != index
                or type(slot["position"]) is not int or not isinstance(slot["after"], str)):
            raise CompilerError("occurrence-slot coverage mismatch: return each source slot once in slot-ID order")
    positions = [slot["position"] for slot in slots]
    expected = list(range(1, len(markers) + 1))
    if sorted(positions) != expected:
        raise CompilerError(f"occurrence-slot order mismatch: expected={expected}, got={positions}; positions must be unique")
    # Defense in depth for injected/non-native backends: English chunks cannot
    # add references, including through JSON Unicode escapes.
    for text in [value["prefix"], *(slot["after"] for slot in slots)]:
        if "__mvd_keep_" in text.lower() or "<" in text or any(ord(c) < 32 for c in text):
            raise CompilerError("occurrence-slot text contains a protected marker, raw reference or control character")
    return value["prefix"] + "".join(markers[slot["slot"] - 1] + slot["after"]
                                      for slot in sorted(slots, key=lambda slot: slot["position"]))
