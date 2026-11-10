"""Typed, non-spoken text printed in the image; never translate its words."""
import re

VISIBLE_TEXT = re.compile(r"<visible>[^<>\r\n]+</visible>")


def outside_visible_text(text):
    return VISIBLE_TEXT.sub("", text)


def map_outside_visible_text(text, transform):
    pieces, start = [], 0
    for match in VISIBLE_TEXT.finditer(text):
        pieces.extend((transform(text[start:match.start()]), match[0]))
        start = match.end()
    pieces.append(transform(text[start:]))
    return "".join(pieces)


def render_visible_text(text):
    # H3 receives ordinary visible-text quotation, not a new native control tag.
    return VISIBLE_TEXT.sub(lambda m: "『" + m[0][len("<visible>"):-len("</visible>")] + "』", text)
