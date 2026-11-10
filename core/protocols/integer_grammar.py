"""Compact canonical decimal intervals for native JSON integer generation."""
import json


def _digit_range(first, last):
    return json.dumps(first) if first == last else f"[{first}-{last}]"


def _any_digits(count):
    return "" if not count else "[0-9]" + (f"{{{count}}}" if count > 1 else "")


def _digits(first, last):
    """Equal-width endpoints, with leading zeroes allowed only in suffixes."""
    if first == last:
        return json.dumps(first)
    remaining = len(first) - 1
    if not remaining:
        return _digit_range(first, last)
    if first[0] == last[0]:
        return json.dumps(first[0]) + " " + _digits(first[1:], last[1:])
    if first[1:] == "0" * remaining and last[1:] == "9" * remaining:
        return _digit_range(first[0], last[0]) + " " + _any_digits(remaining)
    choices = [json.dumps(first[0]) + " " + _digits(first[1:], "9" * remaining)]
    middle_first, middle_last = int(first[0]) + 1, int(last[0]) - 1
    if middle_first <= middle_last:
        choices.append(_digit_range(str(middle_first), str(middle_last)) + " " + _any_digits(remaining))
    choices.append(json.dumps(last[0]) + " " + _digits("0" * remaining, last[1:]))
    return "(" + " | ".join(choices) + ")"


def _nonnegative(first, last):
    choices = []
    for width in range(len(str(first)), len(str(last)) + 1):
        begin = max(first, 0 if width == 1 else 10 ** (width - 1))
        end = min(last, 10 ** width - 1)
        choices.append(_digits(str(begin), str(end)))
    return choices[0] if len(choices) == 1 else "(" + " | ".join(choices) + ")"


def bounded_integer(first, last):
    """Emit compact digit-prefix ranges without enumerating every integer."""
    if type(first) is not int or type(last) is not int or first > last:
        raise ValueError("Bounded integer requires ordered integer endpoints")
    choices = []
    if first < 0:
        choices.append('"-" ' + _nonnegative(max(1, -last), -first))
    if last >= 0:
        choices.append(_nonnegative(max(0, first), last))
    return choices[0] if len(choices) == 1 else "(" + " | ".join(choices) + ")"
