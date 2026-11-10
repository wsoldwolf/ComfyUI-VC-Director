"""Word-boundary-aware native grammar for known identity constraints.

This filters generation, never changes a completed translation. Each DFA state
groups characters sharing a destination into one class to limit sampler work.
"""
import json
from collections import deque


def _word(char):
    return char.isascii() and (char.isalnum() or char == "_")


class AliasGuard:
    def __init__(self, aliases):
        # Native prose already rejects untranslated Japanese. Keep non-ASCII
        # name matching with the final Python validator; never weaken that check.
        names = sorted({name.lower() for name in aliases if name and name.isascii()})
        self.patterns = [("__mvd_keep_", False, False)]
        self.patterns += [(name, _word(name[0]), _word(name[-1])) for name in names]
        self.start = ((), False, False)

    def step(self, state, char):
        active, previous_word, pending = state
        word = _word(char)
        if pending and not word:
            return None
        candidates = [(index, offset + 1) for index, offset in active
                      if self.patterns[index][0][offset] == char.lower()]
        candidates += [(index, 1) for index, (name, left, _) in enumerate(self.patterns)
                       if name[0] == char.lower() and (not left or not previous_word)]
        following, pending = [], False
        for index, offset in candidates:
            name, _, right = self.patterns[index]
            if offset == len(name):
                if not right:
                    return None
                pending = True
            else:
                following.append((index, offset))
        return (tuple(sorted(set(following))), word, pending)

    def accepts(self, text):
        state = self.start
        for char in text:
            state = self.step(state, char)
            if state is None:
                return False
        return not state[2]


def _ranges(values):
    values = sorted(values)
    groups = []
    for value in values:
        if groups and value == groups[-1][1] + 1:
            groups[-1][1] = value
        else:
            groups.append([value, value])
    return "".join(f"\\x{start:02X}" + (f"-\\x{end:02X}" if end != start else "")
                   for start, end in groups)


def guarded_text_rules(aliases, *, json_string=False):
    guard = AliasGuard(aliases)
    excluded = {ord("<")} | ({ord('"'), ord("\\")} if json_string else set())
    # The sentinel represents every non-ASCII character. ASCII-only aliases
    # have no transitions on it; Unicode case variants remain Python-validated.
    alphabet = [chr(code) for code in range(32, 128) if code not in excluded] + ["\u0080"]
    states, pending, rules = {guard.start: 0}, deque([guard.start]), []

    def target(state):
        if state not in states:
            states[state] = len(states)
            pending.append(state)
        return states[state]

    while pending:
        state = pending.popleft()
        groups, escaped = {}, []
        for char in alphabet:
            destination = guard.step(state, char)
            if destination is not None:
                groups.setdefault(target(destination), set()).add(ord(char))
        if json_string:
            for char in ('"', "\\", "/"):
                destination = guard.step(state, char)
                if destination is not None:
                    escaped.append(json.dumps("\\" + char) + f" text-{target(destination)}")
        alternatives = ['""'] if not state[2] else []
        for number, codes in sorted(groups.items()):
            if 128 in codes:
                symbol = "[^" + _ranges(set(range(128)) - (codes - {128})) + "]"
            else:
                symbol = "[" + _ranges(codes) + "]"
            alternatives.append(symbol + f" text-{number}")
        alternatives.extend(escaped)
        rules.append(f"text-{states[state]} ::= " + " | ".join(alternatives))
    return rules
