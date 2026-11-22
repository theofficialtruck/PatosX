# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""What DuckGPT should remember long term: spotting "remember that...", "call me ...", "forget ..." in a
chat message, and keeping private or unsuitable things out of the duck's memory and replies.

DuckGPT has two kinds of memory (see "Memory" in cogs/duckgpt.py). Its immediate memory is simply the
last few minutes of the conversation. Its long term memory only ever holds what a person explicitly
asked it to remember, so it is decided here with fixed patterns, never by asking the model: a model asked
to classify every message guesses, and a guess here would mean the duck "remembering" things nobody
asked it to. Pure functions only: no Discord or database access.

Recognised requests (case insensitive, an optional "hey" / "please" / "can you" in front is fine):

- a fact: "remember that my birthday is in May", "don't forget I play the trumpet", "keep in mind I'm
  vegetarian", "can you remember my dog is called Rex?"
- a name: "call me Bob", "my name is Bob", "my nickname's Bobby", "I go by Bob"
- forgetting: "forget everything", "forget about me", "forget my nickname", "forget that I like cats",
  "clear your memory"

Personal info (emails, phone numbers, addresses, passwords, links) and anything that isn't family
friendly is never saved: the request comes back as ``refused`` so the duck can kindly say no.
"""

import re
from typing import NamedTuple

# ============================================================
# What a message asks the duck to remember or forget
# ============================================================


class MemoryRequest(NamedTuple):
    """One message's memory request. ``action`` is one of:

    - ``save``: remember ``name`` (what to call them) and/or ``fact`` (in their own words)
    - ``forget_all``: forget everything about them
    - ``forget_name``: forget what to call them
    - ``forget``: forget the saved facts about ``forget_text``
    - ``refused``: they asked to save something that must not be kept; ``reason`` is ``private`` or
      ``unsuitable``
    """

    action: str
    name: str | None = None
    fact: str | None = None
    forget_text: str | None = None
    reason: str | None = None


# Longest fact and name that are kept, so one message can't flood every future prompt
MAX_FACT_CHARS = 200
MAX_NAME_CHARS = 32

# Words people put in front of a request: "hey", "ok so", "duck,", "please"
_LEAD = r"^\s*(?:(?:hey|hi|hello|yo|ok(?:ay)?|so|also|and|oh|btw|pls|plz|please|duck|patosx)\b[\s,!.:]*)*"
# Asking politely: "can you", "could u please", "i want you to"
_ASK = (
    r"(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+|pls\s+)?"
    r"|i\s+(?:want|need)\s+(?:you|u)\s+to\s+"
    r"|(?:please|pls|plz)\s+)?"
)

_REMEMBER_RE = re.compile(
    _LEAD
    + _ASK
    + r"(?:remember|memori[sz]e|don'?t\s+forget|do\s+not\s+forget|never\s+forget|keep\s+in\s+mind)\b"
    + r"\s*(?:this\b\s*[:,\-]?\s*)?(?P<that>that\b\s*[:,\-]?\s*)?(?P<fact>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# A fact has to be about the person, not a question, an errand or a new rule for the duck:
# "remember when...?", "remember to drink water", "remember you're evil now"
_NOT_A_FACT_RE = re.compile(
    r"^(?:when|what|how|who|where|why|if|me|us|it|this|that|the\s+time|one\s+time|that\s+time|to\b"
    r"|you\b|ur\b|u\b|your\b|yourself\b)",
    re.IGNORECASE,
)

# "call me Bob" (at the start, or after "you can" / "please" / "just"), "my name is Bob", "I go by Bob"
_NAME_WORD = r"[^\W\d_][\w'\-]{0,31}"
_NAME_CAPTURE = rf"(?P<name>{_NAME_WORD}(?:[ \t]+{_NAME_WORD})?)"
_CALL_ME_RE = re.compile(
    _LEAD + _ASK + r"(?:(?:you|u)\s+(?:can|could|should)\s+|just\s+|from\s+now\s+on,?\s+)?call\s+me\s+" + _NAME_CAPTURE,
    re.IGNORECASE,
)
_MY_NAME_RE = re.compile(
    r"(?:^|[.,!;:]\s*|\b(?:and|btw|also|but|that|hi|hey|hello|yo|so|ok|okay|oh|well|remember|duck|patosx)\s+)"
    r"(?:my\s+(?:nick\s?name|name)\s+is|my\s+(?:nick\s?name|name)'s|i\s+go\s+by)\s+" + _NAME_CAPTURE,
    re.IGNORECASE,
)
_NEGATED_RE = re.compile(r"\b(?:don'?t|do\s+not|never|stop|not)\b[^.!?]{0,20}$", re.IGNORECASE)

# Words that end a name ("call me Bob please" -> "Bob") or show it isn't one ("my name is not important")
_NOT_NAME_WORDS = {
    "a", "about", "actually", "also", "an", "and", "anymore", "anything", "at", "back", "because", "but", "by",
    "crazy", "cuz", "for", "from", "haha", "i", "if", "im", "in", "is", "it", "just", "kinda", "later", "lol",
    "lmao", "maybe", "me", "no", "none", "not", "nothing", "now", "of", "ok", "okay", "on", "or", "please", "pls",
    "really", "secret", "so", "sometime", "sorta", "that", "the", "this", "though", "to", "today", "tomorrow",
    "tonight", "too", "u", "very", "was", "weird", "what", "when", "with", "yes", "you",
}  # fmt: skip

_FORGET_RE = re.compile(
    _LEAD + _ASK + r"(?:forget|erase)\s+(?P<what>.+?)\s*[.!?]*$",
    re.IGNORECASE | re.DOTALL,
)
_WIPE_MEMORY_RE = re.compile(
    _LEAD + _ASK + r"(?:clear|wipe|delete|erase|reset)\s+(?:your|ur|the)\s+(?:memory|memories)\b",
    re.IGNORECASE,
)
_FORGET_ALL_RE = re.compile(
    r"^(?:about\s+)?(?:everything|all|it\s+all|me|all\s+about\s+me|everything\s+about\s+me"
    r"|(?:everything\s+|all\s+)?(?:what|that)\s+you\s+(?:know|remember)(?:\s+about\s+me)?"
    r"|everything\s+i\s+(?:told|said\s+to)\s+(?:you|u))\b",
    re.IGNORECASE,
)
_FORGET_NAME_RE = re.compile(r"^(?:about\s+)?(?:my\s+)?(?:nick\s?name|name)\b", re.IGNORECASE)
# "forget it", "forget that", "forget what I said": about the chat right now, not the long term memory
_FORGET_CHAT_RE = re.compile(
    r"^(?:about\s+)?(?:it|that|this|them|him|her|what\s+i\s+(?:just\s+)?said|i\s+said\s+(?:that|anything)"
    r"|i\s+asked)\b[\s!.?]*(?:lol|lmao|haha)?[\s!.?]*$",
    re.IGNORECASE,
)


def _clean_name(raw: str) -> str | None:
    """The name from a "call me ..." match, cut before the first word that isn't part of a name."""
    words = []
    for word in raw.split():
        if word.lower().strip("'-") in _NOT_NAME_WORDS:
            break
        words.append(word.strip("'-"))
    name = " ".join(word for word in words if word)
    return name[:MAX_NAME_CHARS] or None


def _find_name(text: str) -> str | None:
    """The name a message asks to be called, if any ("call me Bob", "my name is Bob")."""
    for pattern in (_CALL_ME_RE, _MY_NAME_RE):
        for match in pattern.finditer(text):
            if _NEGATED_RE.search(text[: match.start("name")]):
                continue  # "don't call me Bob", "never call me that"
            name = _clean_name(match.group("name"))
            if name:
                return name
    return None


def _find_fact(text: str) -> str | None:
    """The fact a "remember that ..." message asks the duck to keep, in the person's own words."""
    match = _REMEMBER_RE.search(text)
    if not match:
        return None
    fact = " ".join(match.group("fact").split()).strip(" \"'")
    question = fact.endswith("?")
    fact = fact.rstrip(" .!?")
    if _NOT_A_FACT_RE.match(fact) or len(fact.split()) < 2:
        return None
    # "can you remember my name?" is a question; "can you remember that my dog is Rex?" is a request
    if (
        question
        and not match.group("that")
        and not (re.match(r"(?:my|i)\b", fact, re.IGNORECASE) and len(fact.split()) >= 4)
    ):
        return None
    if len(fact) > MAX_FACT_CHARS:
        fact = fact[:MAX_FACT_CHARS].rsplit(" ", 1)[0]
    return fact or None


def _forget_request(text: str) -> MemoryRequest | None:
    """A request to forget something, or None."""
    if _WIPE_MEMORY_RE.search(text):
        return MemoryRequest("forget_all")
    match = _FORGET_RE.search(text)
    if not match:
        return None
    what = " ".join(match.group("what").split())
    if _FORGET_CHAT_RE.match(what):
        return None
    if _FORGET_ALL_RE.match(what):
        return MemoryRequest("forget_all")
    if _FORGET_NAME_RE.match(what):
        return MemoryRequest("forget_name")
    what = re.sub(r"^(?:about|that)\s+", "", what, flags=re.IGNORECASE).strip()
    return MemoryRequest("forget", forget_text=what) if len(what) >= 2 else None


def parse_memory_request(message: str) -> MemoryRequest | None:
    """What this message asks the duck to remember or forget, or None for ordinary chat."""
    text = " ".join(str(message or "").split())
    if not text:
        return None
    forget = _forget_request(text)
    if forget:
        return forget
    name, fact = _find_name(text), _find_fact(text)
    if name and fact and len(fact.split()) <= 5 and comparable(fact).endswith(comparable(name)):
        fact = None  # "remember my name is Bob" is just the name
    if not name and not fact:
        return None
    for saved in (name, fact):
        if saved and is_unsuitable(saved):
            return MemoryRequest("refused", reason="unsuitable")
        if saved and is_private_info(saved):
            return MemoryRequest("refused", reason="private")
    return MemoryRequest("save", name=name, fact=fact)


# ============================================================
# What the duck must never keep or say
# ============================================================

# Personal info the duck never stores (server rule: respect the pond's privacy), and links (no self promo)
_PRIVATE_RE = re.compile(
    r"[\w.+\-]+@[\w\-]+\.[\w.]+"  # email address
    r"|\+?\d{0,3}[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b|\b\d{3}[\s\-.]\d{4}\b"  # phone number
    r"|https?://|www\.|discord\.gg|\b[\w\-]+\.(?:com|net|org|gg|io|tv|me|co|xyz)\b"  # links
    r"|\b(?:password|passcode|passwd|login|my\s+pin\b|pin\s+(?:code|number)"
    r"|(?:home\s+)?address\s+is|my\s+(?:home\s+)?address|i\s+live\s+(?:at|on)\b|ip\s+(?:address\s+)?is"
    r"|social\s+security|ssn\b|credit\s+card|debit\s+card|card\s+number|bank\s+(?:account|details|info)"
    r"|my\s+school\s+is|i\s+go\s+to\s+[\w\s]{1,30}\s+school|my\s+(?:real|full)\s+name)",
    re.IGNORECASE,
)

# Swearing, sexual words and slurs: kept out of the duck's memory and its replies (family friendly server).
# "c[u]nt" is written with a character class so codespell doesn't take it for a typo of "count".
_UNSUITABLE_RE = re.compile(
    r"\b(?:f+u+c+k\w*|fck\w*|sh[i1]t\w*|bullshit|bitch\w*|bastard\w*|c[u]nt\w*|dickhead\w*"
    r"|puss(?:y|ies)|porn\w*|nudes?|sex(?:y|ual)?|nsfw|horny|hentai|boobs?|penis|vagina"
    r"|ass(?:es|hole\w*)?|arsehole\w*|damn\w*|wtf|stfu|whore\w*|slut\w*|n[i1]gg\w*|f[a4]g(?:s|g?ots?)?"
    r"|retard(?:s|ed)?|kys|kill\s+yourself)\b",
    re.IGNORECASE,
)


def is_private_info(text) -> bool:
    """True when text holds personal info the duck must not keep: contact details, an address, a
    password, a link, ..."""
    return isinstance(text, str) and bool(_PRIVATE_RE.search(text))


def is_unsuitable(text) -> bool:
    """True when text has swearing, sexual content or slurs, which don't belong on a family friendly
    server."""
    return isinstance(text, str) and bool(_UNSUITABLE_RE.search(text))


# ============================================================
# Matching saved facts
# ============================================================

_FILLER_WORDS = {"a", "about", "an", "i", "is", "my", "that", "the", "was", "what", "me", "im", "i'm", "am", "are"}


def comparable(text) -> str:
    """Lowercase words only, for comparing facts and replies regardless of punctuation and spacing."""
    return " ".join(re.findall(r"[\w']+", str(text or "").lower()))


def fact_matches(fact: str, forget_text: str) -> bool:
    """True when a saved fact is what "forget ..." refers to: the whole phrase appears in it, or every
    meaningful word does ("forget about my birthday" matches "my birthday is in May")."""
    fact_text, wanted = comparable(fact), comparable(forget_text)
    if not wanted:
        return False
    if wanted in fact_text:
        return True
    keywords = [word for word in wanted.split() if word not in _FILLER_WORDS]
    return bool(keywords) and all(word in fact_text.split() for word in keywords)
