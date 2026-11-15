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

"""The exact math DuckGPT uses instead of letting the model guess (core/mathHelperFuncs.py)."""

import pytest

from core import mathHelperFuncs as m


def statements(message):
    return [problem.statement for problem in m.find_math(message)]


# === every kind of question ======================================================================


@pytest.mark.parametrize(
    "message,expected",
    [
        # arithmetic
        ("great now what is the answer to 45353 times 25345", "45,353 × 25,345 is 1,149,471,785"),
        ("45353*25345", "45,353 × 25,345 is 1,149,471,785"),
        ("what is 45,353 x 25,345?", "45,353 × 25,345 is 1,149,471,785"),
        ("whats 2+2", "2 + 2 is 4"),
        ("what is (3 + 4) * 12?", "(3 + 4) × 12 is 84"),
        ("what is 2 + 3 * 4", "2 + 3 × 4 is 14"),
        ("what is 10 divided by 4", "10 ÷ 4 is 2.5"),
        ("what is 10 over 4", "10 ÷ 4 is 2.5"),
        ("what is 10/3", "10 ÷ 3 is 3.333333"),
        ("what is 100 minus 250", "100 - 250 is -150"),
        ("what is 7 take away 3", "7 - 3 is 4"),
        ("what is 17 mod 5?", "17 % 5 is 2"),
        ("5 * 3", "5 × 3 is 15"),
        ("what is 007 + 3", "7 + 3 is 10"),
        # powers
        ("calculate 2 to the power of 10", "2^10 is 1,024"),
        ("what is 7 squared", "7^2 is 49"),
        ("what is 2 cubed", "2^3 is 8"),
        ("what is -3 squared", "(-3)^2 is 9"),
        ("what is -3^2", "-3^2 is -9"),
        ("what is (2+3) squared", "(2 + 3)^2 is 25"),
        ("what is 2^3^2", "2^3^2 is 512"),
        # word numbers
        ("what is five times three", "5 × 3 is 15"),
        ("whats twenty-five plus one hundred and five", "25 + 105 is 130"),
        ("what is two thousand and five minus five", "2,005 - 5 is 2,000"),
        ("what is one million two hundred thousand times three", "1,200,000 × 3 is 3,600,000"),
        ("what is a hundred plus 5", "100 + 5 is 105"),
        ("what is ninety nine plus one", "99 + 1 is 100"),
        # roots, factorials and friends
        ("what is the square root of 144", "√144 is 12"),
        ("sqrt 2", "√2 is 1.414214"),
        ("what is √81 + 1", "√81 + 1 is 10"),
        ("what is the square root of (16 + 9)", "√(16 + 9) is 5"),
        ("cube root of 27", "∛27 is 3"),
        ("what is the cube root of -8", "∛(-8) is -2"),
        ("what is the 4th root of 81", "the 4th root of 81 is 3"),
        ("what is 5!", "5! is 120"),
        ("what is factorial of 10", "10! is 3,628,800"),
        ("what is 6 factorial", "6! is 720"),
        ("what is the absolute value of -42", "|-42| is 42"),
        # rounding
        ("round 3.14159 to 2 decimal places", "3.14159 rounded to 2 decimal places is 3.14"),
        ("round 1234 to the nearest hundred", "1,234 rounded to the nearest 100 is 1,200"),
        ("round 87 to the nearest ten", "87 rounded to the nearest 10 is 90"),
        ("round 2.6", "2.6 rounded to the nearest whole number is 3"),
        # logs and trig (degrees)
        ("log of 1000", "log(1,000) is 3"),
        ("what is log(1000)", "log(1,000) is 3"),
        ("what is the natural log of 10", "ln(10) is 2.302585"),
        ("log base 2 of 1024", "log base 2 of 1,024 is 10"),
        ("what is sin 30", "sin(30°) is 0.5"),
        ("cos of 60 degrees", "cos(60°) is 0.5"),
        ("tan 45", "tan(45°) is 1"),
        # lists
        ("what is the gcd of 12 and 18", "the GCD of 12 and 18 is 6"),
        ("what is the greatest common factor of 24 and 36", "the GCD of 24 and 36 is 12"),
        ("lcm of 4, 6 and 10", "the LCM of 4, 6 and 10 is 60"),
        ("what is the average of 10, 20, 30 and 40", "the average of 10, 20, 30 and 40 is 25"),
        ("what's the mean of 1, 2 and 4", "the average of 1, 2 and 4 is 2.333333"),
        ("median of 3 1 2", "the median of 3, 1 and 2 is 2"),
        ("sum of 1, 2, 3, 4", "the sum of 1, 2, 3 and 4 is 10"),
        ("product of 2, 3 and 4", "the product of 2, 3 and 4 is 24"),
        ("what is the biggest of 3, 9 and 4", "the largest of 3, 9 and 4 is 9"),
        ("what is the smallest of 3, 9 and 4", "the smallest of 3, 9 and 4 is 3"),
        ("what is the average of 1,000 and 2,000", "the average of 1,000 and 2,000 is 1,500"),
        # fractions of a number
        ("what is half of 50", "50 ÷ 2 is 25"),
        ("two thirds of 90?", "90 × 2 ÷ 3 is 60"),
        ("what is a quarter of 200", "200 ÷ 4 is 50"),
        ("what is three quarters of 100", "100 × 3 ÷ 4 is 75"),
        ("double 21", "21 × 2 is 42"),
        ("what is triple 7", "7 × 3 is 21"),
        # percentages
        ("what is 10% of 100", "10% of 100 is 10"),
        ("10 percent of 80", "10% of 80 is 8"),
        ("what is 12.5% of 80", "12.5% of 80 is 10"),
        ("what is ten percent of two hundred", "10% of 200 is 20"),
        ("what is 15% of $80", "15% of 80 is 12"),
        ("25 is what percent of 200?", "25 out of 200 is 12.5%"),
        ("what percent of 200 is 50", "50 out of 200 is 25%"),
        ("increase 50 by 10%", "50 increased by 10% is 55"),
        ("decrease 80 by 25%", "80 decreased by 25% is 60"),
        ("what is 50 + 10%", "50 plus 10% is 55"),
        ("what is 80 minus 20%", "80 minus 20% is 64"),
        ("whats 20% off $80", "80 with 20% off is 64"),
        ("what is a 15% tip on 60", "a 15% tip on 60 is 9"),
        ("what is the percent change from 50 to 75", "the percent change from 50 to 75 is 50%"),
        ("percentage decrease from 80 to 60?", "the percent change from 80 to 60 is -25%"),
        # yes/no
        ("is 97 prime", "97 is a prime number"),
        ("is 91 a prime number?", "91 is not a prime number"),
        ("is 1 prime", "1 is not a prime number"),
        ("is 12 even", "12 is even"),
        ("is 7 odd?", "7 is odd"),
        ("is 8 odd", "8 is not odd"),
        ("is 91 divisible by 7", "91 is divisible by 7"),
        ("is 100 a multiple of 3", "100 is not a multiple of 3"),
        # unit conversions
        ("convert 5 km to miles", "5 km is 3.106856 miles"),
        ("100 f in c", "100°F is 37.777778°C"),
        ("what is 0 c in fahrenheit", "0°C is 32°F"),
        ("what is 0 celsius in kelvin", "0°C is 273.15 K"),
        ("how many seconds in a day", "1 day is 86,400 seconds"),
        ("how many minutes are in 3 hours", "3 hours is 180 minutes"),
        ("how many feet are in 3 miles", "3 miles is 15,840 feet"),
        ("how many cups in a gallon", "1 gallon is 16 cups"),
        ("60 mph to km/h", "60 mph is 96.56064 km/h"),
        ("10 kg to lbs", "10 kg is 22.046226 pounds"),
        ("what is 1 mile in meters", "1 mile is 1,609.344 meters"),
        ("how many inches in a foot", "1 foot is 12 inches"),
    ],
)
def test_math_questions(message, expected):
    assert statements(message) == [expected]


@pytest.mark.parametrize(
    "message,expected",
    [
        # from the screenshot: these went unanswered before
        ("what is 2342 taken away from 25347", "25,347 - 2,342 is 23,005"),
        ("what is 32674 removed from 784359", "784,359 - 32,674 is 751,685"),
        # other ways of saying it in words
        ("what is 5 subtracted from 20", "20 - 5 is 15"),
        ("what is 5 deducted from 20", "20 - 5 is 15"),
        ("subtract 7 from 50", "50 - 7 is 43"),
        ("take 3 away from 10", "10 - 3 is 7"),
        ("what is 5 less than 20", "20 - 5 is 15"),
        ("what is 5 more than 20", "20 + 5 is 25"),
        ("add 5 to 20", "5 + 20 is 25"),
        ("what is 5 and 3 added together", "5 + 3 is 8"),
        ("what is 6 and 7 multiplied together", "6 × 7 is 42"),
        ("multiply 6 by 7", "6 × 7 is 42"),
        ("divide 20 by 4", "20 ÷ 4 is 5"),
        ("divide 4 into 20", "20 ÷ 4 is 5"),
        ("how many times does 7 go into 56", "56 ÷ 7 is 8"),
        ("what is 12 apples split between 4 friends", "12 ÷ 4 is 3"),
        ("what is 100 shared equally among 5", "100 ÷ 5 is 20"),
        ("what is the difference between 50 and 80", "the difference between 50 and 80 is 30"),
        ("what is the remainder when 17 is divided by 5", "17 % 5 is 2"),
        ("what is the quotient of 20 and 4", "20 ÷ 4 is 5"),
        ("what is the square of 12", "12^2 is 144"),
        ("what is the cube of 3", "3^3 is 27"),
        ("what is the reciprocal of 4", "1 ÷ 4 is 0.25"),
        ("what is 2 to the 10th power", "2^10 is 1,024"),
        ("what is 3 to the fourth", "3^4 is 81"),
        ("what is 50 increased by 7", "50 + 7 is 57"),
        ("what is 50 decreased by 7", "50 - 7 is 43"),
        ("subtract the square root of 16 from 20", "20 - √16 is 16"),
        # comparisons
        ("is 5 bigger than 3", "5 is greater than 3"),
        ("is 2 greater than 9?", "2 is not greater than 9"),
        ("is 3 less than 10", "3 is less than 10"),
        ("is 4 equal to 4", "4 is equal to 4"),
        ("which is bigger, 7 or 9", "the bigger of 7 and 9 is 9"),
        ("which is smaller: 0.5 or 0.25", "the smaller of 0.5 and 0.25 is 0.25"),
    ],
)
def test_math_said_in_words(message, expected):
    assert statements(message) == [expected]


@pytest.mark.parametrize(
    "message",
    [
        "we won 3-1?",  # a "?" alone isn't a request for a calculation
        "i have more than 3 cats",
        "i want 2 more than you",
        "i added 5 friends to the group",
        "i took 2 away from the 5 kids",
        "is it more than 5 miles away",
        "she left 2 days ago and came back 3 days later",
    ],
)
def test_wordy_chat_is_not_math(message):
    assert m.find_math(message) == []


@pytest.mark.parametrize(
    "message,expected",
    [
        ("what is 3 groups of 4 plus some", True),
        ("what is 2 + two halves of 6 minus x", True),
        ("hi", False),
        ("what is the capital of france", False),
        ("what is 42", False),
        ("we won 3-1?", False),
        ("i have 2 cats and 3 dogs", False),
        ("what year is it 2024 or 2025", False),
        ("what is the best number between 1 and 10", False),
    ],
)
def test_looks_like_math(message, expected):
    assert m.looks_like_math(message) is expected


def test_several_questions_in_one_message():
    assert statements("what is 2+2 and 3*3?") == ["2 + 2 is 4", "3 × 3 is 9"]
    assert statements("what is 2+2, 3*3") == ["2 + 2 is 4", "3 × 3 is 9"]
    assert len(m.find_math("what is 1+1, 2+2, 3+3 and 4+4?")) == 3  # capped at 3


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "great job!",
        "i got 5!",  # excitement, not a factorial
        "i ran 5 km to the store",
        "it is 30 c in here lol",
        "i walked 2 miles in an hour",
        "she is 5 feet tall",
        "is it raining",
        "my 2 cents",
        "we won 3-1 last night",
        "i am 5-6 feet tall",
        "the score was 10 - 2 lol",
        "my birthday is 11/14/2024",
        "call me at 555-1234",
        "call 911!",
        "i got 50% of 80 questions right",
        "i gave it 100%",
        "ten percent of the time it works",
        "top 10 of 2024",
        "i have 2 kids and 3 dogs",
        "i have one cat and two dogs",
        "one two three four",
        "no one plus me",
        "the average of my week is bad",
        "how many deserts do you know about",
        "what is love",
        "what is the capital of france",
        "what is 42",
        "i paid $20 for 3 tickets",
        # can't or shouldn't be calculated
        "12 / 0?",
        "what is the square root of -4",
        "what is log of 0",
        "what is tan 90",
        "what is 2**1000000",
        "what is 1000000 factorial",
        "what is 10 ** 1000",
        "what is " + "1+" * 100 + "1",
        "what is __import__('os').system('echo hi')",
        "what is (lambda: 1)()",
        "what is open('x').read()",
        "what is [1, 2][0] + 1",
    ],
)
def test_normal_chat_and_unsafe_input_are_not_treated_as_math(message):
    assert m.find_math(message) == []


def test_excited_exclamation_after_other_math_is_not_a_factorial():
    assert statements("what is 5 times 3!") == ["5 × 3 is 15"]


# === number words =================================================================================


@pytest.mark.parametrize(
    "text,expected",
    [
        ("twenty-five", "25"),
        ("one hundred and five", "105"),
        ("two thousand and five", "2005"),
        ("ninety nine", "99"),
        ("two million five thousand three", "2005003"),
        ("five five", "five five"),  # not one number
        ("thousand thousand", "thousand thousand"),
        ("someone", "someone"),
    ],
)
def test_words_to_numbers(text, expected):
    assert m.words_to_numbers(text) == expected


# === formatting and checking answers =============================================================


@pytest.mark.parametrize(
    "value,expected",
    [(1149471785, "1,149,471,785"), (2.5, "2.5"), (10 / 3, "3.333333"), (-150, "-150"), (84.0, "84"), (0, "0")],
)
def test_format_number(value, expected):
    assert m.format_number(value) == expected


@pytest.mark.parametrize(
    "reply,result,ok",
    [
        ("The answer is 1,149,471,785!", 1149471785, True),
        ("That's 1149471785, quack!", 1149471785, True),
        ("Great, that multiplication problem! The answer is 114,780,635.", 1149471785, False),
        ("It's about 3.33, give or take.", 10 / 3, True),
        ("It's 3.3 or so.", 10 / 3, True),  # rounded to one decimal is fine
        ("It's 3 or so.", 10 / 3, False),  # rounded to a whole number isn't
        ("That's about 3.1 miles.", 3.106856, True),
        ("That's 2.4.", 2.5, False),
        ("12.0 exactly", 12, True),
        ("You end up at negative 150.", -150, True),
        ("You end up at -150.", -150, True),
        ("You end up at 150.", -150, False),
    ],
)
def test_mentions_result(reply, result, ok):
    assert m.mentions_result(reply, result) is ok


@pytest.mark.parametrize(
    "reply,message,kept",
    [
        ("Yep, 97 is prime, no doubt about it!", "is 97 prime", True),
        ("Nope, 97 is not prime.", "is 97 prime", False),
        ("91 isn't prime, it's 7 times 13!", "is 91 prime", True),
        ("Yes, 91 is prime!", "is 91 prime", False),
    ],
)
def test_yes_no_answers_are_checked(reply, message, kept):
    problems = m.find_math(message)
    assert (m.correct_math(reply, problems) == reply) is kept


def test_wrong_answers_are_replaced_with_a_sentence():
    problems = m.find_math("what is the average of 2 and 4 and 9?")
    assert m.correct_math("It's 7, I think!", problems) == "The average of 2, 4 and 9 is 5."
    yes_no = m.find_math("is 91 prime")
    assert m.correct_math("Yes, 91 is prime!", yes_no) == "91 is not a prime number."


def test_math_note_lists_exact_results():
    note = m.math_note(m.find_math("what is 10% of 100 and is 7 odd?"))
    assert "10% of 100 = 10" in note
    assert "7 is odd" in note
    assert "don't try to do the math yourself" in note
