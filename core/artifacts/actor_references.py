"""Bind registered actor names before translation; never guess a pronoun's owner."""
import re

from ..errors import require
from .visible_text import VISIBLE_TEXT, outside_visible_text


SYMBOL = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")
ENGLISH_PERSON_PRONOUNS = ("she", "he", "her", "hers", "his", "him", "they", "them", "their", "theirs",
                           "herself", "himself", "themselves")
PERSON_PRONOUN = re.compile(
    r"彼女(?:ら|達|たち)?|彼(?:ら|等|達|たち)?(?=[はがをにとのもへ、。\s]|$)|前者|後者|"
    + r"\b(?:" + "|".join(ENGLISH_PERSON_PRONOUNS) + r")\b", re.I)
PROTECTED = VISIBLE_TEXT.pattern + r"|@[A-Za-z][A-Za-z0-9_-]*|<(?:Subject|Picture|Video|Audio) [0-9]+>"


class ActorReferences:
    def __init__(self, actors):
        self.aliases = {}
        for actor in actors:
            for name in (actor["name"].strip(), actor["id"]):
                if name:
                    self.aliases.setdefault(name.casefold(), set()).add(actor["id"])
        alternatives = []
        for name in sorted(self.aliases, key=len, reverse=True):
            # Latin names must not match inside another name or an ordinary word.
            pattern = re.escape(name)
            if re.match(r"[A-Za-z0-9_]", name):
                pattern = r"(?<![A-Za-z0-9_])" + pattern
            if re.search(r"[A-Za-z0-9_]$", name):
                pattern += r"(?![A-Za-z0-9_])"
            alternatives.append(pattern)
        self.pattern = re.compile(f"({PROTECTED})|(" + "|".join(alternatives) + ")", re.I) if alternatives else None

    def bind(self, text, path):
        """Only exact declared names/IDs are aliases. Authored dialogue is never passed here."""
        if self.pattern is None:
            return text

        def replace(match):
            if match[1]:
                return match[0]
            owners = self.aliases[match[2].casefold()]
            require(len(owners) == 1, "AMBIGUOUS_ACTOR_NAME", path,
                    f"Actor name {match[2]!r} is shared. Use the explicit @actor_id instead.")
            return "@" + next(iter(owners))

        return self.pattern.sub(replace, text)

    def validate_translation(self, text, path, *, explicit_people=False, pronoun_owner=None, subject_roles=None):
        matches = self.translation_aliases(text)
        details = "; ".join(f"{match['text']!r} (actor_ids={','.join(match['actor_ids'])}, "
                            f"characters={match['start'] + 1}..{match['end']})" for match in matches)
        require(not matches, "TRANSLATION_ACTOR_NAME", path,
                "人物名または裸の人物IDが英訳に混入しました: " + details + ". "
                "Keep every person as its existing protected source slot; do not restore names or bare IDs. "
                "Remove ALL listed identity aliases from ordinary English chunks. If a listed alias is also "
                "an ordinary English word, use a faithful synonym or restructure that phrase. "
                "Do not delete source actions, invent a Subject/slot, guess an actor, or repeat a source slot.")
        if explicit_people:
            translation_pronouns(text, path, pronoun_owner=pronoun_owner, subject_roles=subject_roles)

    def translation_aliases(self, text):
        """Report aliases in ordinary prose without rewriting any translated text."""
        return [dict(text=match[2], actor_ids=sorted(self.aliases[match[2].casefold()]),
                     start=match.start(2), end=match.end(2))
                for match in self.pattern.finditer(text) if match[2]] if self.pattern else []


def _person_subjects(text, subject_roles=None):
    subjects = set(re.findall(r"<Subject [1-9][0-9]*>", outside_visible_text(text)))
    if subject_roles is None:
        return subjects  # Old revisions retain the original interpretation.
    require(all(re.fullmatch(r"<Subject [1-9][0-9]*>", key) and value in {"actor", "prop"}
                for key, value in subject_roles.items()), "TRANSLATION_ROLES", "/", "Invalid Subject role mapping")
    require(subjects <= set(subject_roles), "TRANSLATION_ROLES", "/", "Subject role is missing; do not infer a role")
    return {subject for subject in subjects if subject_roles[subject] == "actor"}


def bound_pronoun_owner(source, owner_subject=None, *, subject_roles=None):
    """A structural field owner and source references must identify just one person."""
    subjects = _person_subjects(source, subject_roles)
    if owner_subject is not None:
        require(re.fullmatch(r"<Subject [1-9][0-9]*>", owner_subject) is not None,
                "TRANSLATION_OWNER", "/", "Translation owner must be a resolved Subject token")
        if subject_roles is not None:
            require(owner_subject in subject_roles, "TRANSLATION_ROLES", "/", "Field owner role is missing")
        if subject_roles is None or subject_roles[owner_subject] == "actor":
            subjects.add(owner_subject)
    return next(iter(subjects)) if len(subjects) == 1 else None


def translation_pronouns(text, path, *, pronoun_owner=None, subject_roles=None):
    # English pronouns can refer to the one structurally bound person. Never
    # infer antecedents from word order or permit a second referenced actor.
    subjects = _person_subjects(text, subject_roles)
    unique_owner = (pronoun_owner is not None and subjects <= {pronoun_owner}
                    and (subject_roles is None or subject_roles.get(pronoun_owner) == "actor"))
    for match in PERSON_PRONOUN.finditer(re.sub(PROTECTED, "", text)):
        if match[0].isascii() and unique_owner:
            continue
        require(False, "AMBIGUOUS_ACTOR_REFERENCE", path,
                f"Unbound person pronoun {match[0]!r}. Rewrite with existing source markers exactly once, "
                "coordinated verbs or an unambiguous noun phrase. Do not create a Subject, @actor_id or new marker. "
                "When the source has no markers, keep the translation marker-free; do not guess another person.")


def no_person_pronouns(text, path):
    match = PERSON_PRONOUN.search(re.sub(PROTECTED, "", text))
    require(match is None, "AMBIGUOUS_ACTOR_REFERENCE", path,
            f"Replace person pronoun {match[0]!r} with the correct explicit @actor_id (or protected Subject marker in translation). "
            "Use the authored action and visible_actor_ids to identify the actor; never assign a pronoun to the nearest name. "
            "Preserve all actions, dialogue, speakers and timing." if match else "Use explicit actor references")


def bind_scene_actor_references(scene, actors):
    """Canonicalize generated prose only; structured owners and exact speech are immutable."""
    references = ActorReferences(actors)
    multiple = len(scene["actor_ids"]) > 1

    def bind(text, path, *, visual=False):
        result = references.bind(text, path)
        if visual and multiple:
            no_person_pronouns(result, path)
        return result

    prefix = scene["id"]
    scene["summary"] = bind(scene["summary"], prefix + "/summary", visual=True)
    if "visual_directions" in scene:
        scene["visual_directions"] = [bind(text, f"{prefix}/visual_directions/{i}", visual=True)
                                      for i, text in enumerate(scene["visual_directions"])]
    for i, description in enumerate(scene["subject_descriptions"]):
        description["text"] = bind(description["text"], f"{prefix}/subject_descriptions/{i}/text")
    for shot in scene["shots"]:
        for event in shot["events"]:
            if event["kind"] in {"action", "camera"}:
                event["text"] = bind(event["text"], event["id"], visual=True)
    for event in scene["sound_events"]:
        event["text"] = bind(event["text"], event["id"])
    for key in ("bgm_description", "ambience_description", "effects_description"):
        if scene["audio"][key]:
            scene["audio"][key] = bind(scene["audio"][key], f"{prefix}/audio/{key}")
