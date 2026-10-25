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
