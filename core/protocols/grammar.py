"""Constrain planning transport to one record containing one JSON object."""
import copy
import json
import re


JSON_RULES = r'''
object ::= "{" ws (member ("," ws member)*)? "}" ws
member ::= string ":" ws value
value ::= object | array | string | number | ("true" | "false" | "null") ws
array ::= "[" ws (value ("," ws value)*)? "]" ws
string ::= "\"" ([^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4}))* "\"" ws
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)? ws
ws ::= [ \t]{0,8}
'''


def _schema_grammar(schema):
    from llama_cpp.llama_grammar import SchemaConverter, RESERVED_NAMES

    class PrefixConverter(SchemaConverter):
        def visit(self, value, name):
            if (value.get("type") == "string" and type(value.get("maxLength")) is int
                    and not any(key in value for key in ("const", "enum", "oneOf", "anyOf", "allOf", "$ref", "format"))):
                maximum, minimum = value["maxLength"], value.get("minLength", 0)
                if "pattern" not in value and type(minimum) is int and 0 <= minimum <= maximum:
                    # Upstream expands bounded strings into thousands of nested
                    # optional expressions. Native GBNF supports compact bounds.
                    # Unlike its generic char rule, exclude literal JSON controls.
                    char = self._add_rule("vcd-json-char",
                        r'[^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})')
                    return self._add_rule(name + "-" if name in RESERVED_NAMES else name or "root",
                        r'"\"" ' + char + f'{{{minimum},{maximum}}}' + r' "\"" space')
                if maximum > 0 and value.get("pattern") == f"^[a-z][a-z0-9_]{{0,{maximum - 1}}}$":
                    return self._add_rule(name + "-" if name in RESERVED_NAMES else name or "root",
                        r'"\"" [a-z] ' + f'[a-z0-9_]{{0,{maximum - 1}}}' + r' "\"" space')
            if (value.get("type") == "integer" and type(value.get("minimum")) is int
                    and type(value.get("maximum")) is int
                    and not any(key in value for key in ("const", "enum", "oneOf", "anyOf", "allOf", "not",
                                                        "multipleOf", "exclusiveMinimum", "exclusiveMaximum", "$ref"))):
                from .integer_grammar import bounded_integer
                return self._add_rule(name + "-" if name in RESERVED_NAMES else name or "root",
                    bounded_integer(value["minimum"], value["maximum"]) + r" [ \t]{0,8}")
            sequence = value.get("x-vcd-event-sequence")
            if sequence:
                prefix = value["prefixItems"]
                if len(prefix) != 1 or value.get("minItems") != 1 or not sequence["required"]:
                    raise ValueError("Unsupported required event sequence")
                camera = self.visit(prefix[0], f"{name}-camera")
                between = self.visit(sequence["between"], f"{name}-between")
                required = [self.visit(item, f"{name}-required-{i}") for i, item in enumerate(sequence["required"])]
                tail = f' ("," space {between})*'
                rule = '"[" space ' + camera + tail
                for event in required:
                    rule += f' "," space {event}' + tail
                rule += ' "]" space'
                return self._add_rule(name + "-" if name in RESERVED_NAMES else name or "root", rule)
            # The installed llama.cpp converter chooses items over prefixItems,
            # silently dropping the mandatory first CAMERA. Handle the one
            # prefix+open-tail form we emit; leave all other schemas upstream.
            if "prefixItems" in value and isinstance(value.get("items"), dict):
                prefix = value["prefixItems"]
                if not prefix or value.get("minItems") != len(prefix) or "maxItems" in value:
                    raise ValueError("Unsupported prefixItems bounds")
                rules = [self.visit(item, f"{name}-prefix-{i}") for i, item in enumerate(prefix)]
                tail = self.visit(value["items"], f"{name}-tail")
                rule = ('"[" space ' + ' "," space '.join(rules)
                        + f' ("," space {tail})* "]" space')
                return self._add_rule(name + "-" if name in RESERVED_NAMES else name or "root", rule)
            return super().visit(value, name)

    converter = PrefixConverter(prop_order={}, allow_fetch=False, dotall=False, raw_pattern=False)
    resolved = converter.resolve_refs(copy.deepcopy(schema), "stdin")
    converter.visit(resolved, "")
    return converter.format_grammar()


def record_grammar(record_type, slot=1, *, payload_schema=None):
    if not re.fullmatch(r"[A-Z][A-Z_]*", record_type) or type(slot) is not int or slot < 1:
        raise ValueError("Invalid planning record type or slot")
    prefix = json.dumps(f"{record_type}\t{slot}\t")
    if payload_schema is not None:
        grammar_schema = payload_schema
        if payload_schema.get("$ref", "").startswith("#/$defs/"):
            # The installed converter skips traversing $defs when the root is a $ref.
            definition = payload_schema["$ref"].removeprefix("#/$defs/")
            grammar_schema = dict(payload_schema["$defs"][definition], **{"$defs": payload_schema["$defs"]})
        generated = _schema_grammar(grammar_schema)
        # Rename rule references without touching JSON string literals named "root".
        generated = re.sub(r'"(?:\\.|[^"\\])*"|(?P<rule>\broot\b)',
                           lambda match: "vcd-payload-root" if match["rule"] else match[0], generated)
        return f'root ::= {prefix} vcd-payload-root "\\n"?\n' + generated
    return f'root ::= {prefix} object "\\n"?\n' + JSON_RULES
