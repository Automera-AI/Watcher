"""Load and validate the intent vocabulary.

You chose YAML so a client can differ without a deploy. The cost of YAML is that a typo is a
runtime failure rather than a build failure, and a runtime failure here means a guest at 2am.
This file buys that back: everything that could be wrong is checked once, at load, and the same
check runs in the build.

    python -m packages.intents packages/intents/intents.yaml packages/intents/clients/*.yaml

What it catches that a human review will not:

  * a `terminal_tool` that is not one of the nine tools
  * a `confusable_with` pointing at an intent that does not exist, or at itself
  * a money or identity intent whose guards have been deleted or weakened
  * an intent that needs proof of identity but does not ask for a booking reference
  * a detail listed in `confirm_before_acting` that is not a detail the intent collects
  * a client override that invents an intent, or loosens a safety rule set here
  * a language used in an example that is not declared at the top
  * an acting intent whose examples are all in a text-only language, so a phone test set
    built from it would be empty
  * an emergency trigger that mixes alphabets, which is how a lookalike character gets in

``yaml`` is imported lazily, inside the two functions that parse it. The application loads the
compiled JSON (``load_compiled``) and must not need a YAML parser installed to do it.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Autonomy = Literal["act", "act_and_notify", "hand_off"]

#: Intents that must never act alone, whatever the file says. Belt and braces: the YAML sets
#: these too, but a bad edit to the YAML should not be able to unset them.
#:
#: ``owner_enquiry`` is here for the second half of "money and owner matters always reach a
#: person" (NEXT-STEPS §13.3). An owner is not a guest, and the receptionist has no business
#: discussing payouts or who is staying in a unit.
MUST_HAND_OFF: frozenset[str] = frozenset(
    {
        "cancel_reservation",
        "billing_question",
        "payment_question",
        "complaint",
        "owner_enquiry",
        "unclear",
    }
)

#: Intents that must require proof of identity before acting.
MUST_VERIFY: frozenset[str] = frozenset(
    {"access_code_request", "modify_reservation", "cancel_reservation", "extend_stay"}
)

#: Intents that touch money and must therefore carry an explicit no-discount rule.
MONEY_INTENTS: frozenset[str] = frozenset({"price_enquiry", "extend_stay"})

#: Channel names that reach a speech model. A client on one of these may only declare languages
#: the base file marks ``spoken``.
VOICE_CHANNELS: frozenset[str] = frozenset({"voice", "phone"})


def _script_of(text: str) -> set[str]:
    """The alphabets a string is written in, ignoring digits, spaces and punctuation.

    Franco-Arabic is Latin letters plus digits, so ``"re7et ghaz"`` is ``{"LATIN"}`` and
    ``"ريحة غاز"`` is ``{"ARABIC"}``. A string that comes back with both is either a genuine
    mix — which no trigger phrase should be — or a lookalike character.
    """
    scripts = set()
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        scripts.add(name.split()[0] if name else "UNKNOWN")
    return scripts


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lang: str
    text: str = Field(min_length=1)


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    means: str = Field(min_length=20, description="Long enough that a person and a model agree.")
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    confirm_before_acting: list[str] | None = None
    terminal_tool: str
    max_autonomy: Autonomy
    needs_verified_identity: bool
    never: list[str] = Field(default_factory=list)
    confusable_with: list[str] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)

    @model_validator(mode="after")
    def _self_consistent(self) -> Intent:
        if self.name in self.confusable_with:
            raise ValueError(f"{self.name}: confusable with itself")

        if self.name in MUST_HAND_OFF and self.max_autonomy != "hand_off":
            raise ValueError(
                f"{self.name}: must be hand_off, found {self.max_autonomy}. "
                "This is a safety rule, not a preference. If you meant to change it, "
                "change MUST_HAND_OFF in schema.py and say why in the commit."
            )

        if self.name in MUST_VERIFY and not self.needs_verified_identity:
            raise ValueError(f"{self.name}: must require proof of identity")

        if self.needs_verified_identity and "reservation_ref" not in (
            self.required_slots + self.optional_slots
        ):
            raise ValueError(
                f"{self.name}: requires proof of identity but never asks for a booking "
                "reference, so there is nothing to check them against"
            )

        if self.name in MONEY_INTENTS and not any("discount" in n for n in self.never):
            raise ValueError(f"{self.name}: touches money but has no rule against discounting")

        known = set(self.required_slots) | set(self.optional_slots)
        if self.confirm_before_acting:
            unknown = [s for s in self.confirm_before_acting if s not in known]
            if unknown:
                raise ValueError(f"{self.name}: confirms details it never collects: {unknown}")

        if self.max_autonomy != "hand_off" and not self.examples:
            raise ValueError(f"{self.name}: acts alone but has no examples to score it against")

        return self


class PropertySystem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    quotable: bool
    note: str


class Quoting(BaseModel):
    """The rules that make quoting a live price safe.

    The `Literal[True]` types are deliberate. These are not settings, they are the reason it is
    acceptable to say a number to a guest at all. Turning one off is a different product, so it
    should be a code change with a reviewer, not a one-word YAML edit.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["property_system_only"]
    requires_quotable_system: Literal[True]
    never_from_memory: Literal[True]
    never_from_knowledge_base: Literal[True]
    max_age_seconds: int = Field(gt=0, le=900)
    on_stale_or_failed: str
    provenance_required: list[str]
    always_state: list[str]
    never: list[str]

    @model_validator(mode="after")
    def _provenance_is_enough_to_recheck(self) -> Quoting:
        """A quote has to be reproducible after the fact, not just correct when it was said."""
        needed = {"property_system_id", "rate_or_quote_id", "fetched_at", "currency"}
        if missing := needed - set(self.provenance_required):
            raise ValueError(
                f"quoting.provenance_required is missing {sorted(missing)}. "
                "Without these a price cannot be re-checked when a guest disputes it."
            )
        return self


class EmergencyTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    any_of: list[str] = Field(min_length=1)
    only_between: list[str] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _no_lookalike_characters(self) -> EmergencyTrigger:
        """A trigger phrase is Latin or it is Arabic. Both at once means a lookalike got in.

        This is not hypothetical. The fire trigger shipped as ``7arі2`` with a Cyrillic ``і``
        (U+0456) where the Latin ``i`` belongs — visually identical, and it would never have
        matched a real message. A silent no-match on the fire trigger is the worst failure in
        the file, and no amount of reading catches it.
        """
        for phrase in self.any_of:
            if len(scripts := _script_of(phrase)) > 1:
                raise ValueError(
                    f"emergency trigger {self.id!r}: {phrase!r} mixes {sorted(scripts)}. "
                    "Almost certainly a lookalike character — this phrase would never match."
                )
        return self


class Emergency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["handoff_to_human"]
    alert: str
    reply_immediately: Literal[True]
    triggers: list[EmergencyTrigger] = Field(min_length=1)


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    #: False for Franco-Arabic: typed, never said out loud. A phone test set filters on this.
    spoken: bool
    note: str


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_before_acting: list[str]
    max_clarifying_turns: int = Field(ge=1, le=5)
    on_max_turns: str
    on_tool_failure: str
    on_no_knowledge: str


class Vocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    vertical: str
    markets: list[str]
    audience: str
    languages: list[Language]
    tools: list[str]
    emergency: Emergency
    defaults: Defaults
    property_systems: list[PropertySystem] = Field(min_length=1)
    quoting: Quoting
    intents: list[Intent]

    @property
    def spoken_languages(self) -> set[str]:
        """The languages a phone test set may draw on (item 3.2). Excludes Franco-Arabic."""
        return {lang.code for lang in self.languages if lang.spoken}

    @model_validator(mode="after")
    def _cross_references(self) -> Vocabulary:
        tools, names = set(self.tools), [i.name for i in self.intents]
        langs = {lang.code for lang in self.languages}

        if len(names) != len(set(names)):
            dupes = {n for n in names if names.count(n) > 1}
            raise ValueError(f"duplicate intents: {sorted(dupes)}")

        known = set(names)

        # Checked before the cross-references below, so deleting a safety-critical intent
        # reports the real problem rather than a dangling reference to it.
        for required in MUST_HAND_OFF | MUST_VERIFY:
            if required not in known:
                raise ValueError(f"safety-critical intent {required!r} is missing from the file")

        for intent in self.intents:
            if intent.terminal_tool not in tools:
                raise ValueError(f"{intent.name}: unknown tool {intent.terminal_tool!r}")
            for other in intent.confusable_with:
                if other not in known:
                    raise ValueError(f"{intent.name}: confusable with unknown {other!r}")
            for ex in intent.examples:
                if ex.lang not in langs:
                    raise ValueError(f"{intent.name}: undeclared language {ex.lang!r}")

        for required in ("handoff_to_human", "take_message"):
            if required not in tools:
                raise ValueError(f"{required!r} must always be available")

        # Franco-Arabic is typed, never spoken. An intent that acts alone has to be testable on
        # a call as well as in chat, so it needs at least one example in a language people say
        # out loud — otherwise the phone test set for it comes out empty and nobody notices.
        spoken = self.spoken_languages
        for intent in self.intents:
            if intent.max_autonomy == "hand_off" or not intent.examples:
                continue
            if not any(ex.lang in spoken for ex in intent.examples):
                raise ValueError(
                    f"{intent.name}: acts alone but every example is in a text-only language "
                    f"({sorted({ex.lang for ex in intent.examples})}). It would have nothing "
                    "to test on a phone call."
                )

        return self


class ClientOverride(BaseModel):
    """A single client's differences. Can narrow the rules, never widen them."""

    model_config = ConfigDict(extra="forbid")

    client: str
    market: Literal["AE", "EG"]
    currency: str
    timezone: str
    channels: list[str]
    languages: list[str]
    property_system: str
    quote_prices: bool
    disabled_intents: list[str] = Field(default_factory=list)
    force_hand_off: list[str] = Field(default_factory=list)
    slot_overrides: dict[str, list[str]] = Field(default_factory=dict)

    def check_against(self, vocab: Vocabulary) -> None:
        known = {i.name for i in vocab.intents}
        langs = {lang.code for lang in vocab.languages}
        systems = {s.id: s for s in vocab.property_systems}

        # The one combination that can put an invented price in front of a guest.
        system = systems.get(self.property_system)
        if system is None:
            raise ValueError(
                f"{self.client}: unknown property system {self.property_system!r}. "
                f"Known: {sorted(systems)}"
            )
        if self.quote_prices and not system.quotable:
            raise ValueError(
                f"{self.client}: quote_prices is on, but {system.id!r} cannot be asked by an "
                "API and cannot be re-checked afterwards, so any number it says would be "
                "unverifiable. Set quote_prices: false and add price_enquiry to force_hand_off."
            )
        if self.quote_prices and "price_enquiry" in self.force_hand_off:
            raise ValueError(
                f"{self.client}: quote_prices is on but price_enquiry is forced to a human. "
                "Pick one."
            )
        if not self.quote_prices and "price_enquiry" not in self.force_hand_off:
            raise ValueError(
                f"{self.client}: quote_prices is off, so price_enquiry must be in "
                "force_hand_off. Otherwise the receptionist has an intent it cannot fulfil."
            )

        for group, label in (
            (self.disabled_intents, "disabled"),
            (self.force_hand_off, "force_hand_off"),
            (list(self.slot_overrides), "slot_overrides"),
        ):
            unknown = [n for n in group if n not in known]
            if unknown:
                raise ValueError(f"{self.client}: {label} names unknown intents {unknown}")

        if bad := [lg for lg in self.languages if lg not in langs]:
            raise ValueError(f"{self.client}: undeclared languages {bad}")

        # The Egyptian example says it out loud — "no phone line, so nothing spoken" — and that
        # is the only reason it may list Franco-Arabic. Put a voice channel on a client that
        # declares a typed-only language and the speech model gets handed "3ayez ahgez", which
        # it will read as noise. The base file already knows which languages are spoken; this is
        # just refusing to let a client contradict it.
        if VOICE_CHANNELS & set(self.channels):
            if typed_only := [lg for lg in self.languages if lg not in vocab.spoken_languages]:
                raise ValueError(
                    f"{self.client}: runs a voice channel but declares text-only languages "
                    f"{typed_only}. Nobody says these out loud — drop them, or drop the channel."
                )

        # A client may turn quoting off. A client may not turn it on where the base file
        # forbids it, and may not re-enable an intent the base file hands off.
        if overreach := [n for n in self.disabled_intents if n in MUST_HAND_OFF | MUST_VERIFY]:
            raise ValueError(
                f"{self.client}: cannot disable safety-critical intents {overreach}. "
                "Disabling them does not make them stop happening, it makes them unhandled."
            )


def _read_yaml(path: Path) -> Any:
    """Parse a YAML file. Imported here, not at module scope, so the runtime JSON path
    (``load_compiled``) does not drag a YAML parser into the shipped image."""
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load(path: Path) -> Vocabulary:
    return Vocabulary.model_validate(_read_yaml(path))


def load_client(path: Path) -> ClientOverride:
    return ClientOverride.model_validate(_read_yaml(path))


def load_compiled(path: Path) -> Vocabulary:
    """Load the compiled JSON the application ships with.

    Prefer this at runtime. It is ~400x faster than parsing the YAML, and more importantly it
    cannot be invalid, because `compile.py` refuses to write a file that does not validate.
    Fall back to the YAML only in development, where being able to edit and reload matters more
    than the milliseconds.
    """
    return Vocabulary.model_validate(json.loads(path.read_text(encoding="utf-8")))


_DEFAULT_DIR = Path(__file__).resolve().parent
_cached: Vocabulary | None = None


def default_vocabulary() -> Vocabulary:
    """The shipped vocabulary, loaded once and held in memory.

    Prefers the compiled JSON and falls back to the YAML, which is the whole point of the
    compile step: fast and provably valid in a deployed image, editable in development. Cached
    because the vocabulary is read per turn and parsing it per turn is exactly the thing the
    README says nothing should do.

    A compiled file **older than the YAML is ignored**. Otherwise editing ``intents.yaml`` in
    development changes nothing until you remember to recompile, and you debug against data you
    are no longer looking at. In a deployed image there is no YAML to compare against, so the
    JSON is used unconditionally.
    """
    global _cached
    if _cached is None:
        compiled = _DEFAULT_DIR / "build" / "intents.json"
        source = _DEFAULT_DIR / "intents.yaml"
        if compiled.exists() and (
            not source.exists() or compiled.stat().st_mtime >= source.stat().st_mtime
        ):
            _cached = load_compiled(compiled)
        else:
            _cached = load(source)
    return _cached


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m packages.intents intents.yaml [clients/*.yaml]")
        return 2

    base_path, *client_paths = [Path(a) for a in argv]
    try:
        vocab = load(base_path)
    except Exception as exc:
        print(f"FAIL {base_path}\n  {exc}")
        return 1

    acting = sum(1 for i in vocab.intents if i.max_autonomy == "act")
    notify = sum(1 for i in vocab.intents if i.max_autonomy == "act_and_notify")
    human = sum(1 for i in vocab.intents if i.max_autonomy == "hand_off")
    examples = sum(len(i.examples) for i in vocab.intents)

    print(f"OK   {base_path}  v{vocab.version}")
    print(
        f"     {len(vocab.intents)} intents: {acting} act, {notify} act and notify, {human} human"
    )
    print(f"     {examples} examples across {len(vocab.languages)} languages")
    print(f"     {len(vocab.emergency.triggers)} emergency triggers, checked before everything")

    failed = False
    for path in client_paths:
        try:
            client = load_client(path)
            client.check_against(vocab)
            print(f"OK   {path}  {client.client} ({client.market}, {client.currency})")
        except Exception as exc:
            print(f"FAIL {path}\n  {exc}")
            failed = True

    if examples < 60:
        print(f"\nNOTE {examples} examples. Roadmap item 2.5 wants about 60 before the accuracy")
        print("     number means anything. These are seeds, not the finished set.")

    return 1 if failed else 0
