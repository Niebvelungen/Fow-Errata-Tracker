"""Heuristic text normalization for comparing card rules text.

Force of Will re-templated its rules language several times across the game's
history, so a naive text diff of a card vs its reprint flags ~1000 cards that
are pure wording rewrites, not real errata. This module collapses the known
template/wording differences so that what's left over is much more likely to be
a genuine rules change.

It is intentionally heuristic and imperfect (per the project decision): the goal
is to shrink the candidate list, and the HTML word-diff view is what makes the
remaining candidates fast to review by hand. Add patterns to ``_REPLACEMENTS``
as you discover more template noise.

The same normalization (symbol + bracket stripping) is also applied to OCR text
vs JSON text, since printed symbol icons can't be read as text.
"""
from __future__ import annotations

import re

# Ordered (pattern, replacement) pairs applied to lowercased text BEFORE symbols
# and bracket tags are stripped. Order matters — earlier rules can feed later ones.
_REPLACEMENTS: list[tuple[str, str]] = [
    # Printed cards show reminder text in parentheses that the JSON omits — strip
    # it so it never counts as a difference. (JSON rarely has parens; safe both sides.)
    (r"\([^)]*\)", " "),
    # "Solo Mode" is just a section marker (bracketed in JSON, plain on the card);
    # drop it so the marker word itself never counts as a difference.
    (r"\[?\s*solo mode\s*\]?", " "),
    # --- timing / activation templates -------------------------------------
    # Old judgment wording -> new, bracketed or bare (OCR often drops brackets).
    (r"\[j-activate\]\s*pay", "[judgment]"),
    (r"\[j-activate\]", "[judgment]"),
    (r"\bj-activate\b", "judgment"),
    # "[Activate] Pay {cost}:" -> "{cost}:" (the prefix is pure template).
    (r"\[activate\]\s*pay", "[activate]"),
    # [Activate]/[Continuous] timing tags are dropped by the newer template;
    # remove them whether bracketed or bare (OCR reads them without brackets).
    (r"\bactivate\b", ""),
    (r"\bcontinuous\b", ""),
    # Unify guillemet-style arrows with their ASCII equivalents so they never
    # count as a difference: ≪/« == << and ≫/» == >>.
    (r"[≪«]", "<<"),
    (r"[≫»]", ">>"),
    # Old verbose self-trigger phrasing == the newer "[Enter]" tag.
    (r"when this card enters (?:the|your) field", "[enter]"),
    # Old "⇒" trigger arrow == the newer ">>>" arrow (a format update).
    (r"⇒", ">>>"),
    # "[Enter] >>> X" / "[Enter]: X" both mean the same trigger arrow.
    (r">>>", ":"),
    # --- vocabulary drift ---------------------------------------------------
    # Collapse the "J/" prefix in all forms (J/resonator, J/rulers, J/spell, …).
    (r"\bj/", ""),
    (r"\bmain deck\b", "deck"),
    (r"\bdarkness\b", "dark"),
    # "into the field under its owner's control" <-> "into its owner's field".
    (r"into the field under its owner's control", "into its owner's field"),
    (r"into the field under your control", "into your field"),
    # "an entity named" / "a J/ruler named" -> drop the wrapper, keep the name.
    (r"an entity named\s+", ""),
    (r"a (?:j/)?ruler named\s+", ""),
    (r"a (?:j/)?resonator named\s+", ""),
    (r"an? entity you control with the (.+?) type", r"\1"),
    (r"entities you control with the (.+?) type", r"\1"),
    # "your Ruler is X" / "your J-Ruler is X" -> "you control X".
    (r"your j-?ruler is", "you control"),
    (r"your ruler is", "you control"),
    (r"if you control a (?:j/)?ruler named", "if you control"),
    (r"\bnamed\b", ""),
    # Stat blocks: "[+200/+0]" vs "[+200/0]" etc. handled by symbol strip below.
]

_SYMBOL_RE = re.compile(r"\{[^}]*\}")          # {W}, {Rest}, {1}, {Rest} icons
_BRACKET_RE = re.compile(r"\[[^\]]*\]")        # [Activate], [Continuous], [Flying]
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def normalize(abilities: list[str] | str) -> str:
    """Return a normalized, comparable token string for a card's rules text."""
    text = abilities if isinstance(abilities, str) else " ".join(abilities)
    text = text.lower()
    for pattern, repl in _REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    # Keep the words inside [tags] (e.g. flying, swiftness) but drop the brackets,
    # so a keyword that genuinely disappears still shows as a difference.
    text = _BRACKET_RE.sub(lambda m: " " + m.group(0)[1:-1] + " ", text)
    # Curly braces hold either mana/number symbols ({W}, {1}, {Rest}-tap, {}) or,
    # in OCR, a keyword icon as a word ({Enter}, {Flying}). Keep keyword words
    # (alphabetic, >=3 chars) so they match a square-bracket [Enter]; drop the
    # rest as symbols.
    text = _SYMBOL_RE.sub(_curly_repl, text)
    text = _NONWORD_RE.sub(" ", text)
    return " ".join(text.split())


# Multi-letter curly tokens that are game SYMBOLS, not keyword abilities.
_CURLY_SYMBOLS = {"rest"}


def _curly_repl(m: re.Match) -> str:
    inner = m.group(0)[1:-1]
    if inner.isalpha() and len(inner) >= 3 and inner not in _CURLY_SYMBOLS:
        return f" {inner} "
    return " "


def tokens(abilities: list[str] | str) -> list[str]:
    return normalize(abilities).split()


def sorted_tokens(abilities: list[str] | str) -> list[str]:
    """Order-insensitive token bag. Two texts with the same words in a different
    order (text shifted to another point on the card) compare equal."""
    return sorted(tokens(abilities))
