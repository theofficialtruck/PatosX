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

"""Exact math for DuckGPT: finds the math a chat message asks for and calculates it.

Language models predict likely looking digits instead of calculating, so DuckGPT never lets the model do
math. ``find_math`` spots the calculations in a message, works them out exactly, and DuckGPT hands the
results to the model (``math_note``) and checks its reply (``correct_math``).

What it understands (numbers as digits or words, "45,353" or "two thousand and five"):

- arithmetic: + - × ÷ %, "times", "plus", "divided by", "mod", powers ("^", "squared", "to the power of"),
  brackets, and fractions of a number ("half of 50", "two thirds of 90", "double 21")
- roots and more: square/cube/nth roots (also √ ∛), factorials ("5!"), absolute value, rounding
  ("round 3.14159 to 2 decimal places", "to the nearest hundred"), log / ln / log base n, sin / cos / tan
  (in degrees)
- lists: average/mean, median, sum, product, largest/smallest, GCD and LCM ("lcm of 4, 6 and 10")
- percentages: "10% of 100", "25 is what percent of 200", "increase 50 by 10%", "80 minus 20%",
  "20% off 80", "a 15% tip on 60", "percent change from 50 to 75"
- yes/no questions: "is 97 prime", "is 12 even/odd", "is 91 divisible by 7"
- unit conversions: length, weight, volume, time, speed and temperature ("5 km to miles",
  "100 f in c", "how many seconds in a day")

Everything is evaluated by walking the parsed expression and allowing only numbers, these operators and
the functions in ``_FUNCTIONS``; nothing is ever passed to eval, and huge powers, factorials and results
are refused.
"""

import ast
import math
import operator
import re
import statistics
from typing import NamedTuple

# ============================================================
# Results
# ============================================================


def format_number(value) -> str:
    """Format a number the way people write it: 1,149,471,785 / 2.5 / 0.333333."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}"
    if value != 0 and (abs(value) >= 1e15 or abs(value) < 1e-6):
        return f"{value:.6g}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


class MathProblem(NamedTuple):
    """One calculation from a message. ``result`` is a number, or True/False for a yes/no question, in
    which case ``expression`` is the "yes" statement and ``negative`` the "no" one."""

    expression: str
    result: int | float | bool
    unit: str = ""
    negative: str = ""

    @property
    def is_yes_no(self) -> bool:
        return isinstance(self.result, bool)

    @property
    def answer(self) -> str:
        if self.is_yes_no:
            return "yes" if self.result else "no"
        return f"{format_number(self.result)}{self.unit}"

    @property
    def statement(self) -> str:
        """The answer as a sentence fragment: "45,353 × 25,345 is 1,149,471,785" / "97 is a prime number"."""
        if self.is_yes_no:
            return self.expression if self.result else self.negative
        return f"{self.expression} is {self.answer}"


# ============================================================
# Number words ("two thousand and five" -> 2005)
# ============================================================

_UNIT_WORDS = {
    word: value
    for value, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
_TENS_WORDS = {
    word: (i + 2) * 10
    for i, word in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"])
}
_SCALE_WORDS = {"hundred": 100, "thousand": 10**3, "million": 10**6, "billion": 10**9, "trillion": 10**12}
_NUMBER_WORD = "|".join(sorted([*_UNIT_WORDS, *_TENS_WORDS, *_SCALE_WORDS], key=len, reverse=True))
# "one hundred and five": an "and" right after hundred/thousand/... is part of the number
_SCALE_AND_RE = re.compile(
    rf"\b(hundred|thousand|million|billion|trillion)\s+and\s+(?=(?:{_NUMBER_WORD})\b)", re.IGNORECASE
)
_NUMBER_PHRASE_RE = re.compile(rf"\b(?:{_NUMBER_WORD})(?:(?:\s+|-)(?:{_NUMBER_WORD}))*\b", re.IGNORECASE)


def _words_value(words: list[str]) -> int | None:
    """The value of a sequence of number words, or None if it isn't one number ("five five")."""
    total, current, last, last_scale = 0, 0, None, None
    for word in words:
        if word in _UNIT_WORDS:
            # a unit can only follow nothing, a tens word (twenty-five) or a scale word (hundred five)
            if last in ("unit", "teen") or (last == "tens" and _UNIT_WORDS[word] >= 10):
                return None
            current += _UNIT_WORDS[word]
            last = "teen" if _UNIT_WORDS[word] >= 10 else "unit"
        elif word in _TENS_WORDS:
            if last in ("unit", "teen", "tens"):
                return None
            current += _TENS_WORDS[word]
            last = "tens"
        elif word == "hundred":
            if last == "hundred" or current >= 100:
                return None
            current = (current or 1) * 100
            last = "hundred"
        else:  # thousand / million / ...: each must be smaller than the one before ("two million five thousand")
            scale = _SCALE_WORDS[word]
            if last_scale is not None and scale >= last_scale:
                return None
            total += (current or 1) * scale
            current, last, last_scale = 0, "scale", scale
    return total + current


def words_to_numbers(text: str) -> str:
    """Rewrite number words as digits; anything that isn't one clear number ("one two three") stays."""
    text = _SCALE_AND_RE.sub(r"\1 ", text)

    def convert(match: re.Match) -> str:
        value = _words_value(re.split(r"[\s-]+", match.group().lower()))
        return match.group() if value is None else str(value)

    return _NUMBER_PHRASE_RE.sub(convert, text)


# ============================================================
# Safe evaluation
# ============================================================

MAX_MATH_EXPRESSION_LENGTH = 160
MAX_MATH_RESULT = 10**100  # anything bigger isn't a chat answer


class _NotMath(ValueError):
    """Not a supported calculation, or one with no sensible result."""


def _is_int(value) -> bool:
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _sqrt(x):
    if x < 0:
        raise _NotMath("square root of a negative number")
    if _is_int(x) and math.isqrt(int(x)) ** 2 == int(x):
        return math.isqrt(int(x))
    return math.sqrt(x)


def _root(x, n):
    if not _is_int(n) or not 2 <= n <= 100:
        raise _NotMath("unsupported root")
    n = int(n)
    if x < 0 and n % 2 == 0:
        raise _NotMath("even root of a negative number")
    exact = round(abs(x) ** (1 / n))
    if _is_int(x) and exact**n == abs(int(x)):
        return exact if x >= 0 else -exact
    return math.copysign(abs(x) ** (1 / n), x)


def _factorial(x):
    if not _is_int(x) or not 0 <= x <= 200:
        raise _NotMath("unsupported factorial")
    return math.factorial(int(x))


def _round(x, digits=0):
    if not _is_int(digits) or not -15 <= digits <= 10:
        raise _NotMath("unsupported rounding")
    value = round(x, int(digits))
    return int(value) if digits <= 0 else value


def _log(x, base=None):
    if x <= 0 or (base is not None and (base <= 0 or base == 1)):
        raise _NotMath("log of a non-positive number")
    return math.log(x) if base is None else math.log(x, base)


def _trig(function):
    def apply(degrees):
        if function is math.tan and math.isclose(math.cos(math.radians(degrees)), 0, abs_tol=1e-12):
            raise _NotMath("tan is undefined here")
        return round(function(math.radians(degrees)), 12)  # sin(30) -> 0.5, not 0.49999999999999994

    return apply


def _whole_numbers(function):
    def apply(*values):
        if not all(_is_int(v) for v in values):
            raise _NotMath("needs whole numbers")
        return function(*(int(v) for v in values))

    return apply


# name: (min args, max args, implementation)
_FUNCTIONS = {
    "sqrt": (1, 1, _sqrt),
    "cbrt": (1, 1, lambda x: _root(x, 3)),
    "root": (2, 2, _root),
    "fact": (1, 1, _factorial),
    "abs": (1, 1, abs),
    "round": (1, 2, _round),
    "ln": (1, 1, _log),
    "log10": (1, 1, lambda x: _log(x, 10)),
    "log": (2, 2, _log),
    "sin": (1, 1, _trig(math.sin)),
    "cos": (1, 1, _trig(math.cos)),
    "tan": (1, 1, _trig(math.tan)),
    "gcd": (2, 50, _whole_numbers(math.gcd)),
    "lcm": (2, 50, _whole_numbers(math.lcm)),
    "mean": (2, 50, lambda *v: statistics.fmean(v)),
    "median": (2, 50, lambda *v: statistics.median(v)),
    "total": (2, 50, lambda *v: sum(v)),
    "product": (2, 50, lambda *v: math.prod(v)),
    "max": (2, 50, max),
    "min": (2, 50, min),
    "diff": (2, 2, lambda a, b: abs(a - b)),
}
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _checked(value):
    if isinstance(value, complex) or abs(value) > MAX_MATH_RESULT or (isinstance(value, float) and math.isnan(value)):
        raise _NotMath("result out of range")
    return value


def _evaluate(node):
    """Evaluate a parsed expression made only of numbers, the operators above and _FUNCTIONS."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (
            abs(right) > 1000 or (abs(left) > 1 and abs(right) * math.log10(abs(left)) > 100)
        ):
            raise _NotMath("power too large")
        try:
            return _checked(_BINARY_OPERATORS[type(node.op)](left, right))
        except (ZeroDivisionError, OverflowError) as e:
            raise _NotMath(str(e)) from e
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        low, high, function = _FUNCTIONS[node.func.id]
        if not low <= len(node.args) <= high:
            raise _NotMath("wrong number of arguments")
        try:
            return _checked(function(*(_evaluate(arg) for arg in node.args)))
        except (ValueError, OverflowError, ZeroDivisionError, statistics.StatisticsError) as e:
            raise _NotMath(str(e)) from e
    raise _NotMath(f"unsupported: {type(node).__name__}")


# ============================================================
# Showing a calculation to people ("45,353 × 25,345", "√144", "5!")
# ============================================================

_SYMBOLS = {ast.Add: " + ", ast.Sub: " - ", ast.Mult: " × ", ast.Div: " ÷ ", ast.Mod: " % ", ast.Pow: "^"}
_PRECEDENCE = {ast.Add: 1, ast.Sub: 1, ast.Mult: 2, ast.Div: 2, ast.Mod: 2, ast.Pow: 4}
_LIST_NAMES = {
    "gcd": "the GCD of",
    "lcm": "the LCM of",
    "mean": "the average of",
    "median": "the median of",
    "total": "the sum of",
    "product": "the product of",
    "max": "the largest of",
    "min": "the smallest of",
}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _precedence(node) -> int:
    if isinstance(node, ast.BinOp):
        return _PRECEDENCE[type(node.op)]
    if isinstance(node, ast.UnaryOp):
        return 3
    return 5


def _render(node, minimum: int = 0) -> str:
    """Render a parsed expression for people, adding brackets only where they're needed."""
    if isinstance(node, ast.Constant):
        text = format_number(node.value)
        return f"({text})" if minimum > 3 and node.value < 0 else text
    if isinstance(node, ast.UnaryOp):
        text = ("-" if isinstance(node.op, ast.USub) else "+") + _render(node.operand, 3)
        return f"({text})" if minimum > 3 else text
    if isinstance(node, ast.BinOp):
        own = _PRECEDENCE[type(node.op)]
        right_side = own + 1 if not isinstance(node.op, (ast.Add, ast.Mult)) else own
        left = _render(node.left, own + 1 if isinstance(node.op, ast.Pow) else own)
        text = (
            f"{left}{_SYMBOLS[type(node.op)]}{_render(node.right, own if isinstance(node.op, ast.Pow) else right_side)}"
        )
        return f"({text})" if own < minimum else text
    if isinstance(node, ast.Call):
        name, args = node.func.id, node.args
        arg = _render(args[0], 5) if args else ""
        if name == "sqrt":
            text = f"√{arg}"
        elif name == "cbrt":
            text = f"∛{arg}"
        elif name == "root":
            text = f"the {_ordinal(int(_evaluate(args[1])))} root of {_render(args[0])}"
        elif name == "fact":
            text = f"{arg}!"
        elif name == "abs":
            text = f"|{_render(args[0])}|"
        elif name == "round":
            digits = int(_evaluate(args[1])) if len(args) > 1 else 0
            if digits > 0:
                text = f"{_render(args[0])} rounded to {digits} decimal place{'s' if digits > 1 else ''}"
            elif digits < 0:
                text = f"{_render(args[0])} rounded to the nearest {10**-digits:,}"
            else:
                text = f"{_render(args[0])} rounded to the nearest whole number"
        elif name in ("ln", "log10"):
            text = f"{'ln' if name == 'ln' else 'log'}({_render(args[0])})"
        elif name == "log":
            text = f"log base {_render(args[1])} of {_render(args[0])}"
        elif name in ("sin", "cos", "tan"):
            text = f"{name}({_render(args[0])}°)"
        elif name == "diff":
            text = f"the difference between {_render(args[0])} and {_render(args[1])}"
        else:
            items = [_render(a) for a in args]
            text = f"{_LIST_NAMES[name]} {', '.join(items[:-1])} and {items[-1]}"
        return f"({text})" if minimum > 4 and name not in ("sqrt", "cbrt", "fact", "abs") else text
    raise _NotMath("can't render")


# ============================================================
# Reading the math out of a message
# ============================================================

_NUM = r"(-?\d+(?:\.\d+)?)"
_NUMBER = r"-?\d+(?:\.\d+)?"
# a number, or something in brackets, as the input of a function ("square root of (16 + 9)")
_OPERAND = r"(-?\d+(?:\.\d+)?|\([^()]*\))"
# a list of numbers: "1, 2, 3 and 4" / "1 2 3" (thousands like 1,000 are kept together)
_LIST_NUMBER = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"
_LIST = rf"((?:{_LIST_NUMBER})(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s+)(?:{_LIST_NUMBER}))+)"
_PERCENT = r"(?:\s*%|\s*per\s*cent\b|\s*percent\b)"

_LIST_FUNCTIONS = (
    (r"(?:gcd|gcf|hcf|greatest\s+common\s+(?:divisor|factor)|highest\s+common\s+factor)\s+(?:of\s+)?", "gcd"),
    (r"(?:lcm|(?:least|lowest)\s+common\s+multiple)\s+(?:of\s+)?", "lcm"),
    (r"(?:average|mean)\s+(?:of\s+)?", "mean"),
    (r"median\s+(?:of\s+)?", "median"),
    (r"(?:sum|total)\s+of\s+", "total"),
    (r"product\s+of\s+", "product"),
    (r"(?:max(?:imum)?|biggest|largest|highest)\s+(?:number\s+)?(?:of|in|out\s+of)\s+", "max"),
    (r"(?:min(?:imum)?|smallest|lowest)\s+(?:number\s+)?(?:of|in|out\s+of)\s+", "min"),
)
_FRACTIONS = {"half": 2, "halves": 2, "third": 3, "thirds": 3, "quarter": 4, "quarters": 4, "fourth": 4}
_FRACTIONS |= {"fourths": 4, "fifth": 5, "fifths": 5, "tenth": 10, "tenths": 10}
_FUNCTION_PHRASES = (
    (rf"\b(?:the\s+)?square\s+root\s+of\s+{_OPERAND}", r"sqrt(\1)"),
    (rf"√\s*{_OPERAND}", r"sqrt(\1)"),
    (rf"\bsqrt\s+{_OPERAND}", r"sqrt(\1)"),
    (rf"\b(?:the\s+)?cube\s+root\s+of\s+{_OPERAND}", r"cbrt(\1)"),
    (rf"∛\s*{_OPERAND}", r"cbrt(\1)"),
    (rf"\b(?:the\s+)?(\d+)(?:st|nd|rd|th)\s+root\s+of\s+{_OPERAND}", r"root(\2, \1)"),
    (rf"\b(?:the\s+)?factorial\s+of\s+{_OPERAND}", r"fact(\1)"),
    (r"(?<![\d.])(\d+)\s+factorial\b", r"fact(\1)"),
    (rf"\b(?:the\s+)?absolute\s+value\s+of\s+{_OPERAND}", r"abs(\1)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(\d+)\s+(?:decimal(?:\s+places?)?|dp|places?)\b", r"round(\1, \2)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(?:the\s+)?nearest\s+(?:whole\s+number|integer|one)\b", r"round(\1)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(?:the\s+)?nearest\s+ten\b", r"round(\1, -1)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(?:the\s+)?nearest\s+(?:hundred|100)\b", r"round(\1, -2)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(?:the\s+)?nearest\s+(?:thousand|1000)\b", r"round(\1, -3)"),
    (rf"\bround\s+{_OPERAND}\s+to\s+(?:the\s+)?nearest\s+10\b", r"round(\1, -1)"),
    (rf"\bround(?:ed)?\s+{_OPERAND}", r"round(\1)"),
    (rf"\blog(?:arithm)?\s+base\s+{_NUM}\s+of\s+{_OPERAND}", r"log(\2, \1)"),
    (rf"\b(?:the\s+)?natural\s+log(?:arithm)?\s+of\s+{_OPERAND}", r"ln(\1)"),
    (rf"\bln\s*{_OPERAND}", r"ln(\1)"),
    # (not a "log(1024, 2)" the "log base" rule above already wrote)
    (rf"\blog(?:arithm)?(?:10)?\s*(?:of\s+)?(?!\([^()]*,){_OPERAND}", r"log10(\1)"),
    # "sin 30" / "sine of 30 degrees" / "cosine 60" / "tangent of 45°" -> sin(30) / cos(60) / tan(45)
    (
        rf"\b(sine|sin|cosine|cos|tangent|tan)\s*(?:of\s+)?{_OPERAND}(?:\s*(?:°|degrees?))?",
        lambda match: f"{match.group(1)[:3].lower()}({match.group(2)})",
    ),
    (rf"\b(?:double|twice)\s+{_OPERAND}", r"(\1 * 2)"),
    (rf"\btriple\s+{_OPERAND}", r"(\1 * 3)"),
    (rf"\bquadruple\s+{_OPERAND}", r"(\1 * 4)"),
)
_FRACTION_OF_RE = re.compile(
    rf"\b(?:(a|an|\d+)\s+)?({'|'.join(_FRACTIONS)})\s+of\s+(?:a\s+|an\s+)?{_OPERAND}", re.IGNORECASE
)

# A number, something in brackets, or a function call the rules above already wrote ("sqrt(16)")
_TERM = r"((?:[a-z]+\d*)?\([^()]*\)|-?\d+(?:\.\d+)?)"
_ORDINAL_WORDS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
_ORDINAL_WORDS |= {"ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}
_POWER_ORDINAL = r"(\d+)(?:st|nd|rd|th)|(" + "|".join(_ORDINAL_WORDS) + ")"


def _to_the_power(match: re.Match) -> str:
    """ "2 to the 10th power" / "3 to the fourth" -> (2 ** 10) / (3 ** 4)"""
    exponent = match.group(2) or _ORDINAL_WORDS[match.group(3).lower()]
    return f"({match.group(1)} ** {exponent})"


# Math said in words, often with the numbers in reverse order ("2342 taken away from 25347" is
# 25347 - 2342). Rewritten to plain arithmetic, with brackets so the order of operations stays right.
_WORD_PHRASES = (
    (rf"{_TERM}\s+(?:taken\s+away|taken|removed|subtracted|deducted|minused)\s+from\s+{_TERM}", r"(\2 - \1)"),
    (rf"\b(?:subtract|deduct|remove|minus)\s+{_TERM}\s+from\s+{_TERM}", r"(\2 - \1)"),
    (rf"\btake\s+(?:away\s+)?{_TERM}\s+(?:away\s+)?from\s+{_TERM}", r"(\2 - \1)"),
    (rf"{_TERM}\s+(?:less|fewer)\s+than\s+{_TERM}", r"(\2 - \1)"),
    (rf"{_TERM}\s+more\s+than\s+{_TERM}", r"(\2 + \1)"),
    (rf"\badd\s+{_TERM}\s+(?:to|and|plus|with)\s+{_TERM}", r"(\1 + \2)"),
    (rf"{_TERM}\s+and\s+{_TERM}\s+(?:added|summed)(?:\s+together|\s+up)?\b", r"(\1 + \2)"),
    (rf"{_TERM}\s+and\s+{_TERM}\s+multiplied(?:\s+together)?\b", r"(\1 * \2)"),
    (rf"\bmultiply\s+{_TERM}\s+(?:by|and|with|times)\s+{_TERM}", r"(\1 * \2)"),
    (rf"\bdivide\s+{_TERM}\s+(?:by|over)\s+{_TERM}", r"(\1 / \2)"),
    (rf"\bdivide\s+{_TERM}\s+into\s+{_TERM}", r"(\2 / \1)"),  # "divide 4 into 20" is 20 / 4
    (rf"\bhow\s+many\s+times\s+(?:does|do|will|can|would)\s+{_TERM}\s+(?:go|fit)\s+into\s+{_TERM}", r"(\2 / \1)"),
    (rf"{_TERM}\s+(?:goes|go|fits|fit)\s+into\s+{_TERM}(?:\s+how\s+many\s+times)?", r"(\2 / \1)"),
    (rf"\b(?:the\s+)?difference\s+(?:between|of)\s+{_TERM}\s+and\s+{_TERM}", r"diff(\1, \2)"),
    (rf"\b(?:the\s+)?remainder\s+(?:when|of|if|after)\s+{_TERM}\s+(?:is\s+)?divided\s+by\s+{_TERM}", r"(\1 % \2)"),
    (rf"\b(?:the\s+)?quotient\s+of\s+{_TERM}\s+and\s+{_TERM}", r"(\1 / \2)"),
    # "12 apples split between 4 friends" / "100 shared equally among 5" (after the remainder rule, so
    # "the remainder when 17 is divided by 5" stays a remainder; plain "divided by" is an operator below)
    (
        (
            rf"{_TERM}\s+(?:[a-z]+\s+)?(?:(?:split|shared)\s+(?:up\s+)?(?:equally\s+|evenly\s+)?"
            rf"(?:between|among|amongst|by)|divided\s+(?:up\s+)?(?:equally\s+|evenly\s+)?(?:between|among|amongst))"
            rf"\s+{_TERM}"
        ),
        r"(\1 / \2)",
    ),
    (rf"\b(?:the\s+)?square\s+of\s+{_TERM}", r"(\1) ** 2"),
    (rf"\b(?:the\s+)?cube\s+of\s+{_TERM}", r"(\1) ** 3"),
    (rf"\b(?:the\s+)?reciprocal\s+of\s+{_TERM}", r"(1 / \1)"),
    (rf"{_TERM}\s+to\s+the\s+(?:{_POWER_ORDINAL})(?:\s+power)?\b", _to_the_power),
    (rf"{_TERM}\s+(?:increased|raised)\s+by\s+{_TERM}(?!\s*(?:%|per))", r"(\1 + \2)"),
    (rf"{_TERM}\s+(?:decreased|reduced|lowered)\s+by\s+{_TERM}(?!\s*(?:%|per))", r"(\1 - \2)"),
)
_FACTORIAL_RE = re.compile(r"(?<![\d.])(\d+)\s*!(?!=)")
# Word and symbol operators, rewritten to Python operators before parsing
_OPERATORS = (
    # "-3 squared" means (-3)², so squared/cubed take the sign with them (unlike "-3^2", which is -9)
    (rf"{_OPERAND}\s*squared\b", r"(\1) ** 2 "),
    (rf"{_OPERAND}\s*cubed\b", r"(\1) ** 3 "),
    (r"\bto\s+the\s+power\s+of\b|\braised\s+to(?:\s+the\s+power\s+of)?\b|\^|\*\*", " ** "),
    (r"\bmultiplied\s+by\b|\btimes\b|[×✕∗·]|(?<=\d)\s*[xX]\s*(?=[\d(])", " * "),
    (r"\bdivided\s+by\b|\bover\b(?=\s*[\d(])|÷", " / "),
    (r"\bplus\b|\badded\s+to\b", " + "),
    (r"\bminus\b|\btake\s+away\b|−", " - "),
    (r"\bmod(?:ulo)?\b", " % "),
)
_FUNCTION_NAMES = "|".join(sorted(_FUNCTIONS, key=len, reverse=True))
# A run that can only be math: digits, operators, brackets, commas and known function calls
_MATH_RUN_RE = re.compile(rf"(?:[\d.+\-*/%(),\s]|(?<![a-z])(?:{_FUNCTION_NAMES})(?=\s*\())+", re.IGNORECASE)
# The message asks for a result: "what's", "calculate", "how many", "convert", "is 97 prime", ends with "?"
_MATH_CUE_RE = re.compile(
    r"\b(?:what(?:'?s|\s+is|\s+are|\s+would)|whats|calculate|compute|work\s+out|how\s+(?:much|many)|answer\s+to|"
    r"equals?|convert|solve|find|tell\s+me|give\s+me|figure\s+out)\b|^\s*(?:so\s+|and\s+|but\s+|hey\s+)?(?:is|does)\b|=",
    re.IGNORECASE,
)
# Math words and symbols, for spotting a math question the calculator couldn't read
_MATH_WORDS_RE = re.compile(
    r"\b(?:plus|minus|times|multiply|multiplied|multiplication|divide|divided|division|subtract|subtracted|"
    r"subtraction|add(?:ed)?|sum|product|difference|take|taken|takes|removed|"
    r"deducted|less|more|percent\w*|squared?|cubed?|root|power|remainder|quotient|average|mean|half|double|triple|"
    r"split|shared?|each|per|groups?\s+of)\b"
    r"|\d\s*[-+*/×÷^%x]\s*\d",
    re.IGNORECASE,
)
# A clear request for an answer (unlike the cue above, "is ..." questions don't count here)
_ASKS_FOR_ANSWER_RE = re.compile(
    r"\b(?:what(?:'?s|\s+is|\s+are|\s+would)|whats|calculate|compute|work\s+out|how\s+(?:much|many)|answer\s+to|"
    r"solve|figure\s+out)\b|=",
    re.IGNORECASE,
)


# --- percentages ---------------------------------------------------------------------------------


def _number(text: str):
    value = float(text)
    return int(value) if value.is_integer() and "." not in text else value


def _percent_problems(text: str) -> tuple[str, list[MathProblem]]:
    """Find the percentage questions in text; returns the text with them removed, and the problems."""
    found = []

    def take(pattern: str, build):
        nonlocal text

        def replace(match: re.Match) -> str:
            problem = build(*(_number(g) for g in match.groups()))
            if problem is None:
                return match.group()
            found.append(problem)
            return " "

        text = re.sub(pattern, replace, text, flags=re.IGNORECASE)

    f = format_number
    lead = r"(?<![\d.])"
    take(
        rf"{lead}{_NUM}{_PERCENT}\s*tip\s+(?:on|for)\s+(?:a\s+|an\s+)?{_NUM}",
        lambda p, x: MathProblem(f"a {f(p)}% tip on {f(x)}", x * p / 100),
    )
    take(
        rf"{lead}{_NUM}{_PERCENT}\s*(?:off|discount\s+(?:on|off))\s+(?:of\s+)?(?:a\s+|an\s+)?{_NUM}",
        lambda p, x: MathProblem(f"{f(x)} with {f(p)}% off", x * (100 - p) / 100),
    )
    take(
        rf"\b(?:increase|raise)\s+{_NUM}\s+by\s+{_NUM}{_PERCENT}",
        lambda x, p: MathProblem(f"{f(x)} increased by {f(p)}%", x * (100 + p) / 100),
    )
    take(
        rf"\b(?:decrease|reduce|lower|cut)\s+{_NUM}\s+by\s+{_NUM}{_PERCENT}",
        lambda x, p: MathProblem(f"{f(x)} decreased by {f(p)}%", x * (100 - p) / 100),
    )
    take(
        rf"{lead}{_NUM}\s*(?:\+|plus)\s*{_NUM}{_PERCENT}",
        lambda x, p: MathProblem(f"{f(x)} plus {f(p)}%", x * (100 + p) / 100),
    )
    take(
        rf"{lead}{_NUM}\s*(?:-|minus|less)\s*{_NUM}{_PERCENT}",
        lambda x, p: MathProblem(f"{f(x)} minus {f(p)}%", x * (100 - p) / 100),
    )
    take(
        rf"\bpercent(?:age)?\s+(?:change|increase|decrease|difference)\s+(?:from|between)\s+{_NUM}\s+(?:to|and)\s+{_NUM}",
        lambda a, b: (
            None if a == 0 else MathProblem(f"the percent change from {f(a)} to {f(b)}", (b - a) / a * 100, "%")
        ),
    )
    take(
        rf"{lead}{_NUM}\s+(?:is|as)\s+what\s+(?:percent(?:age)?|%)\s+of\s+{_NUM}",
        lambda part, whole: (
            None if whole == 0 else MathProblem(f"{f(part)} out of {f(whole)}", part / whole * 100, "%")
        ),
    )
    take(
        rf"\bwhat\s+(?:percent(?:age)?|%)\s+of\s+{_NUM}\s+is\s+{_NUM}",
        lambda whole, part: (
            None if whole == 0 else MathProblem(f"{f(part)} out of {f(whole)}", part / whole * 100, "%")
        ),
    )
    take(
        rf"{lead}{_NUM}{_PERCENT}\s*of\s+(?:a\s+|an\s+)?{_NUM}",
        lambda p, x: MathProblem(f"{f(p)}% of {f(x)}", p * x / 100),
    )
    return text, found


# --- yes/no questions ----------------------------------------------------------------------------


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0:
        return False
    return all(n % d for d in range(3, math.isqrt(n) + 1, 2))


def _yes_no_problems(text: str) -> tuple[str, list[MathProblem]]:
    """Find "is 97 prime" / "is 12 even" / "is 91 divisible by 7" questions."""
    found = []
    f = format_number

    def prime(match: re.Match) -> str:
        n = _number(match.group(1))
        if not isinstance(n, int) or abs(n) > 10**12:
            return match.group()
        found.append(MathProblem(f"{f(n)} is a prime number", _is_prime(n), negative=f"{f(n)} is not a prime number"))
        return " "

    def parity(match: re.Match) -> str:
        n, kind = _number(match.group(1)), match.group(2).lower()
        if not isinstance(n, int):
            return match.group()
        found.append(
            MathProblem(f"{f(n)} is {kind}", (n % 2 == 0) == (kind == "even"), negative=f"{f(n)} is not {kind}")
        )
        return " "

    def divisible(wording: str):
        def check(match: re.Match) -> str:
            n, d = _number(match.group(1)), _number(match.group(2))
            if not isinstance(n, int) or not isinstance(d, int) or d == 0:
                return match.group()
            yes, no = f"{f(n)} is {wording} {f(d)}", f"{f(n)} is not {wording} {f(d)}"
            found.append(MathProblem(yes, n % d == 0, negative=no))
            return " "

        return check

    text = re.sub(rf"\bis\s+{_NUM}\s+(?:a\s+)?prime(?:\s+number)?\b", prime, text, flags=re.IGNORECASE)
    text = re.sub(rf"\bis\s+{_NUM}\s+(?:an?\s+)?(even|odd)(?:\s+number)?\b", parity, text, flags=re.IGNORECASE)
    text = re.sub(
        rf"\bis\s+{_NUM}\s+(?:evenly\s+)?divisible\s+by\s+{_NUM}", divisible("divisible by"), text, flags=re.IGNORECASE
    )
    text = re.sub(rf"\bis\s+{_NUM}\s+a\s+multiple\s+of\s+{_NUM}", divisible("a multiple of"), text, flags=re.IGNORECASE)

    def compare(wording: str, test):
        def check(match: re.Match) -> str:
            # "what is 5 more than 3" is addition, not a yes/no question
            if re.search(r"\b(?:what|whats|what's|how\s+much)\s*$", match.string[: match.start()], re.IGNORECASE):
                return match.group()
            a, b = _number(match.group(1)), _number(match.group(2))
            found.append(
                MathProblem(f"{f(a)} is {wording} {f(b)}", test(a, b), negative=f"{f(a)} is not {wording} {f(b)}")
            )
            return " "

        return check

    bigger, smaller = r"(?:more|greater|bigger|larger|higher)", r"(?:less|smaller|fewer|lower)"
    text = re.sub(
        rf"\bis\s+{_NUM}\s+{bigger}\s+than\s+{_NUM}", compare("greater than", operator.gt), text, flags=re.IGNORECASE
    )
    text = re.sub(
        rf"\bis\s+{_NUM}\s+{smaller}\s+than\s+{_NUM}", compare("less than", operator.lt), text, flags=re.IGNORECASE
    )
    text = re.sub(
        rf"\b(?:is|does)\s+{_NUM}\s+(?:equal(?:s)?(?:\s+to)?|the\s+same\s+as)\s+{_NUM}",
        compare("equal to", operator.eq),
        text,
        flags=re.IGNORECASE,
    )

    def which(kind: str):
        def pick(match: re.Match) -> str:
            a, b = _number(match.group(1)), _number(match.group(2))
            found.append(MathProblem(f"the {kind} of {f(a)} and {f(b)}", max(a, b) if kind == "bigger" else min(a, b)))
            return " "

        return pick

    text = re.sub(rf"\bwhich\s+is\s+{bigger}[,:]?\s+{_NUM}\s+or\s+{_NUM}", which("bigger"), text, flags=re.IGNORECASE)
    text = re.sub(rf"\bwhich\s+is\s+{smaller}[,:]?\s+{_NUM}\s+or\s+{_NUM}", which("smaller"), text, flags=re.IGNORECASE)
    return text, found


# --- unit conversions ----------------------------------------------------------------------------

# key: (kind, factor to the base unit, pattern, singular name, plural name). Temperatures are special.
_UNITS = {
    "km": ("length", 1000, r"km|kilomet(?:er|re)s?", "km", "km"),
    "m": ("length", 1, r"m|met(?:er|re)s?", "meter", "meters"),
    "cm": ("length", 0.01, r"cm|centimet(?:er|re)s?", "cm", "cm"),
    "mm": ("length", 0.001, r"mm|millimet(?:er|re)s?", "mm", "mm"),
    "mile": ("length", 1609.344, r"mi|miles?", "mile", "miles"),
    "yard": ("length", 0.9144, r"yds?|yards?", "yard", "yards"),
    "foot": ("length", 0.3048, r"ft|foot|feet", "foot", "feet"),
    "inch": ("length", 0.0254, r"inch(?:es)?", "inch", "inches"),
    "kg": ("mass", 1, r"kgs?|kilo(?:gram)?s?", "kg", "kg"),
    "g": ("mass", 0.001, r"g|grams?|grammes?", "gram", "grams"),
    "lb": ("mass", 0.45359237, r"lbs?|pounds?", "pound", "pounds"),
    "oz": ("mass", 0.028349523125, r"oz|ounces?", "ounce", "ounces"),
    "tonne": ("mass", 1000, r"tonnes?|metric\s+tons?", "tonne", "tonnes"),
    "l": ("volume", 1, r"l|lit(?:er|re)s?", "liter", "liters"),
    "ml": ("volume", 0.001, r"ml|millilit(?:er|re)s?", "ml", "ml"),
    "gallon": ("volume", 3.785411784, r"gal(?:lon)?s?", "gallon", "gallons"),
    "quart": ("volume", 0.946352946, r"qts?|quarts?", "quart", "quarts"),
    "pint": ("volume", 0.473176473, r"pts?|pints?", "pint", "pints"),
    "cup": ("volume", 0.2365882365, r"cups?", "cup", "cups"),
    "ms": ("time", 0.001, r"ms|milliseconds?", "millisecond", "milliseconds"),
    "s": ("time", 1, r"s|secs?|seconds?", "second", "seconds"),
    "min": ("time", 60, r"mins?|minutes?", "minute", "minutes"),
    "h": ("time", 3600, r"h|hrs?|hours?", "hour", "hours"),
    "day": ("time", 86400, r"days?", "day", "days"),
    "week": ("time", 604800, r"wks?|weeks?", "week", "weeks"),
    "year": ("time", 31536000, r"yrs?|years?", "year", "years"),
    "kmh": ("speed", 1000 / 3600, r"km/h|kph|kmph|kilomet(?:er|re)s?\s+(?:per|an|a)\s+hour", "km/h", "km/h"),
    "mph": ("speed", 1609.344 / 3600, r"mph|miles?\s+(?:per|an|a)\s+hour", "mph", "mph"),
    "mps": ("speed", 1, r"m/s|met(?:er|re)s?\s+per\s+second", "m/s", "m/s"),
    "c": ("temperature", None, r"°\s*c|degrees?\s+c(?:elsius)?|celsius|centigrade|c", "°C", "°C"),
    "f": ("temperature", None, r"°\s*f|degrees?\s+f(?:ahrenheit)?|fahrenheit|f", "°F", "°F"),
    "k": ("temperature", None, r"kelvins?|k", "K", "K"),
}
# Longest patterns first, so "km/h" wins over "km" and "milliseconds" over "ms"
_UNIT_ALTERNATION = "|".join(
    f"(?:{spec[2]})" for _key, spec in sorted(_UNITS.items(), key=lambda item: -len(item[1][2]))
)
_UNIT = rf"((?:{_UNIT_ALTERNATION}))(?![a-z/])"


def _unit_key(text: str) -> str | None:
    text = " ".join(text.lower().split())
    for key, (_kind, _factor, pattern, _one, _many) in _UNITS.items():
        if re.fullmatch(pattern, text, flags=re.IGNORECASE):
            return key
    return None


def _to_kelvin(value, key):
    return {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[key]


def _from_kelvin(value, key):
    return {"c": value - 273.15, "f": (value - 273.15) * 9 / 5 + 32, "k": value}[key]


def _unit_name(key: str, amount) -> str:
    _kind, _factor, _pattern, one, many = _UNITS[key]
    name = one if amount == 1 else many
    return name if name.startswith("°") else f" {name}"


def _convert(amount, source: str, target: str) -> MathProblem | None:
    source_key, target_key = _unit_key(source), _unit_key(target)
    if source_key is None or target_key is None or source_key == target_key:
        return None
    kind, source_factor = _UNITS[source_key][:2]
    target_kind, target_factor = _UNITS[target_key][:2]
    if kind != target_kind:
        return None
    if kind == "temperature":
        result = round(_from_kelvin(_to_kelvin(amount, source_key), target_key), 10)
    else:
        result = amount * source_factor / target_factor
    if abs(result) > MAX_MATH_RESULT:
        return None
    result = int(result) if isinstance(result, float) and result.is_integer() and abs(result) < 1e15 else result
    return MathProblem(
        f"{format_number(amount)}{_unit_name(source_key, amount)}", result, _unit_name(target_key, result)
    )


def _conversion_problems(text: str) -> tuple[str, list[MathProblem]]:
    """Find "5 km to miles" / "how many seconds in a day" style conversions."""
    found = []

    def direct(match: re.Match) -> str:
        problem = _convert(_number(match.group(1)), match.group(2), match.group(3))
        if problem is None:
            return match.group()
        found.append(problem)
        return " "

    def how_many(match: re.Match) -> str:
        amount = _number(match.group(2)) if match.group(2) else 1
        problem = _convert(amount, match.group(3), match.group(1))
        if problem is None:
            return match.group()
        found.append(problem)
        return " "

    text = re.sub(
        rf"(?<![\d.]){_NUM}\s*{_UNIT}\s+(?:to|in|into|as|in\s+to)\s+{_UNIT}",
        direct,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\bhow\s+many\s+{_UNIT}\s+(?:are\s+|is\s+)?(?:there\s+)?(?:in|is|are|make|equals?)\s+"
        rf"(?:a\s+|an\s+|one\s+)?(?:{_NUM}\s*)?{_UNIT}",
        how_many,
        text,
        flags=re.IGNORECASE,
    )
    return text, found


# --- putting it together -------------------------------------------------------------------------


def _list_call(name: str):
    def replace(match: re.Match) -> str:
        numbers = [n.replace(",", "") for n in re.findall(_LIST_NUMBER, match.group(1))]
        return f"{name}({', '.join(numbers)})" if len(numbers) >= 2 else match.group()

    return replace


def _fraction_of(match: re.Match) -> str:
    count = match.group(1)
    numerator = 1 if count is None or count.lower() in ("a", "an") else int(count)
    denominator = _FRACTIONS[match.group(2).lower()]
    operand = match.group(3)
    return f"({operand} / {denominator})" if numerator == 1 else f"({operand} * {numerator} / {denominator})"


def _arithmetic_problems(run: str) -> list[MathProblem]:
    """The calculations in a run of math characters ("45353 * 25345", "sqrt(144) + 1, 2 + 2")."""
    expression = " ".join(run.split()).strip(" ,")
    if not expression or len(expression) > MAX_MATH_EXPRESSION_LENGTH:
        return []
    try:
        tree = ast.parse(expression, mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return []
    found = []
    for node in tree.elts if isinstance(tree, ast.Tuple) else [tree]:
        # a lone number ("42", "-5") isn't a calculation
        if isinstance(node, ast.Constant) or (isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant)):
            continue
        try:
            found.append(MathProblem(_render(node), _evaluate(node)))
        except (_NotMath, RecursionError, TypeError):
            continue
    return found


def find_math(message: str) -> list[MathProblem]:
    """The calculations a message asks for, at most 3. Empty unless the message is asking for a result
    ("what is 45353 times 25345", "is 97 prime?", "5 km to miles") or is nothing but the math, so dates,
    phone numbers, scores and percentages mentioned in normal chat are left alone."""
    text = words_to_numbers(message or "")
    text = re.sub(r"[$€£¥](?=\s*-?\d)", "", text)  # "$80" -> "80"
    for pattern, name in _LIST_FUNCTIONS:  # before thousands commas are removed, so lists stay lists
        text = re.sub(rf"\b(?:the\s+)?{pattern}{_LIST}", _list_call(name), text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)  # 45,353 -> 45353
    text = re.sub(r"(?<![\d.])0+(?=\d)", "", text)  # 007 -> 7
    found = []
    for take in (_conversion_problems, _yes_no_problems, _percent_problems):
        text, problems = take(text)
        found.extend(problems)

    # "5!" is a factorial, unless it's just an excited "!" ending a message that has other math in it
    def factorial(match: re.Match) -> str:
        at_end = not text[match.end() :].strip(" ?!.")
        has_other_math = re.search(
            r"\d\s*(?:[-+*/x×÷^]|times|plus|minus|divided)", text[: match.start()], re.IGNORECASE
        )
        return match.group(1) if at_end and has_other_math else f"fact({match.group(1)})"

    text = _FACTORIAL_RE.sub(factorial, text)
    for pattern, replacement in _FUNCTION_PHRASES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = _FRACTION_OF_RE.sub(_fraction_of, text)
    for pattern, replacement in _WORD_PHRASES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for pattern, replacement in _OPERATORS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    def take_arithmetic(match: re.Match) -> str:
        problems = _arithmetic_problems(match.group())
        if not problems:
            return match.group()
        found.extend(problems)
        return " "

    leftover = _MATH_RUN_RE.sub(take_arithmetic, text)
    if not found:
        return []
    only_math = len(re.sub(r"[\s?!.=,]", "", leftover)) <= 3  # e.g. "45353 x 25345" or "10% of 100 pls"
    if not (only_math or _MATH_CUE_RE.search(message or "")):
        return []
    return found[:3]


# ============================================================
# Using the results
# ============================================================


def looks_like_math(message: str) -> bool:
    """True when a message clearly asks for a calculation (an answer request, two or more numbers and a
    math word or symbol) even though find_math couldn't read it, so the model can be told not to guess."""
    text = words_to_numbers(message or "")
    numbers = re.findall(r"\d+(?:[.,]\d+)*", text)
    return len(numbers) >= 2 and bool(_ASKS_FOR_ANSWER_RE.search(text)) and bool(_MATH_WORDS_RE.search(text))


UNREADABLE_MATH_NOTE = (
    "\nThis message looks like a math question, but it couldn't be calculated exactly. Don't guess or make up "
    "a number. Say casually that you're not sure what they mean, and ask them to write it out with symbols "
    "like + - × ÷. Stay on this topic."
)


def math_note(problems: list[MathProblem]) -> str:
    """The exact results, added to DuckGPT's system prompt for this message so the model doesn't guess."""
    results = "; ".join(p.statement if p.is_yes_no else f"{p.expression} = {p.answer}" for p in problems)
    return (
        f"\nCalculated exactly for this message: {results}. Use exactly these results, written just like "
        "that, and don't try to do the math yourself."
    )


def _numbers_in(text: str) -> list[str]:
    """The numbers written in text, as strings (so the number of decimals is known)."""
    text = re.sub(r"(?<=\d)[, ](?=\d{3}(?!\d))", "", text)  # 1,149,471,785 -> 1149471785
    text = re.sub(r"\b(?:negative|minus)\s+(?=\d)", "-", text, flags=re.IGNORECASE)
    return re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", text)


def mentions_result(text: str, result) -> bool:
    """True when text contains the result. Whole numbers must be exact; other results may be rounded to
    one or more decimal places, the way people write them ("3.1 miles", "37.78°C"), but not to a whole
    number."""
    for number in _numbers_in(text):
        if "." not in number:
            if _is_int(result) and int(number) == int(result):
                return True
            continue
        decimals = len(number.split(".")[1])
        if math.isclose(float(number), round(result, decimals), abs_tol=10 ** -(decimals + 3)):
            return True
    return False


_NEGATION_RE = re.compile(r"\b(?:not|isn'?t|aren'?t|nope|never)\b|\bno\b(?!\s+doubt)", re.IGNORECASE)


def _reply_is_right(reply: str, problem: MathProblem) -> bool:
    if problem.is_yes_no:
        return bool(_NEGATION_RE.search(reply)) != problem.result
    return mentions_result(reply, problem.result)


def correct_math(reply: str, problems: list[MathProblem]) -> str:
    """Keep the model's reply if every result in it is right; otherwise answer with the exact results."""
    if all(_reply_is_right(reply, problem) for problem in problems):
        return reply
    print(f"[DuckGPT] Wrong math in the model's reply ({reply!r}), answering with the calculated result")
    answer = ", and ".join(problem.statement for problem in problems) + "."
    return answer[0].upper() + answer[1:]
