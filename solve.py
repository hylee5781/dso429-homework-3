#!/usr/bin/env python3
"""Answer every released round of the DSO 429 Hourly Quiz.

Fetches questions.csv from the course repo, works each question out with
exact arithmetic, and writes answers/round-NNNN.json for every round.
Standard library only, so it runs anywhere Python 3.8+ is installed.

    python3 solve.py           # fetch the questions, write answers/
    python3 solve.py --check   # also grade practice rounds 1-48 against the answer keys

Exits with status 1 if a question released in the last three hours (the window
in which a corrected answer still counts) has no option that matches the
computed answer exactly, so the scheduled GitHub Actions run fails and emails.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

QUIZ_REPO = "FaisalXL/dso429-hourly-quiz"
ANSWERS_DIR = Path(__file__).resolve().parent / "answers"
CORRECTION_WINDOW = timedelta(hours=3)
LETTERS = "ABCD"
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")
PERIODS_PER_YEAR = {"annually": 1, "semi-annually": 2, "semiannually": 2,
                    "quarterly": 4, "monthly": 12, "daily": 365}

NUM = r"\$?(-?[\d,]*\.?\d+)"  # 12 / 1.5726 / $31,500.00 / -15.1 (sign kept, $ and % left outside)


def num(text: str) -> float:
    return float(text.replace(",", "").replace("$", ""))


def parse_date(text: str) -> date:
    day, month, year = text.split()
    return date(int(year), MONTHS.index(month.lower()) + 1, int(day))


# --- Question templates ------------------------------------------------------
# Each solver gets the regex groups of its template and returns the exact answer
# (a number, or a date). Patterns include what the question asks for, so a
# reworded question falls through to "unrecognized" instead of being misread.

SOLVERS = []


def template(pattern):
    def register(fn):
        SOLVERS.append((re.compile(pattern, re.IGNORECASE), fn))
        return fn
    return register


@template(rf"investment of {NUM} grows at {NUM}% compounded ({'|'.join(PERIODS_PER_YEAR)})\. "
          rf"What is its value after {NUM} years")
def compound_growth(principal, rate, period, years):
    m = PERIODS_PER_YEAR[period.lower()]
    return num(principal) * (1 + num(rate) / 100 / m) ** (m * num(years))


@template(rf"listed at {NUM} is discounted by {NUM}%\. What is the sale price")
def sale_price(price, pct):
    return num(price) * (1 - num(pct) / 100)


@template(rf"sells {NUM} units at {NUM} each\. What is total revenue")
def total_revenue(units, price):
    return num(units) * num(price)


@template(rf"invoice for {NUM} is converted to [a-z ]+ at {NUM} [a-z ]+ per US dollar")
def currency_conversion(amount, rate):
    return num(amount) * num(rate)


@template(rf"Region A had {NUM} orders averaging {NUM}; region B had {NUM} orders averaging {NUM}\. "
          r"What is the overall average order value")
def blended_average(n_a, avg_a, n_b, avg_b):
    n_a, avg_a, n_b, avg_b = map(num, (n_a, avg_a, n_b, avg_b))
    return (n_a * avg_a + n_b * avg_b) / (n_a + n_b)


@template(rf"generates {NUM} in revenue against {NUM} in cost of goods sold\. What is the gross margin")
def gross_margin(revenue, cogs):
    return (num(revenue) - num(cogs)) / num(revenue) * 100


@template(rf"Fixed costs are {NUM}\. Each unit sells for {NUM} with a variable cost of {NUM}\. "
          r"How many units must be sold to break even")
def break_even_units(fixed, price, variable):
    # Left unrounded: the closest option is the answer "to the nearest unit".
    return num(fixed) / (num(price) - num(variable))


@template(r"holds (.+?)\. If one is drawn at random, what is the probability it is an? (\w+) ticket")
def ticket_probability(pool, kind):
    counts = {k.lower(): int(n) for n, k in re.findall(r"(\d+) (\w+) tickets?", pool)}
    return counts[kind.lower()] / sum(counts.values()) * 100


@template(rf"moved from {NUM} to {NUM}\. What is the percentage change")
def percentage_change(old, new):
    return (num(new) - num(old)) / num(old) * 100


@template(rf"A {NUM} [a-z ]*budget is split between two channels in the ratio (\d+):(\d+)\. "
          r"What does the (first|second) channel receive")
def ratio_split(total, a, b, which):
    share = a if which.lower() == "first" else b
    return num(total) * num(share) / (num(a) + num(b))


@template(r"series runs ([-\d., ]+)\. If the pattern continues, what is the next value")
def next_in_series(terms):
    xs = [num(t) for t in terms.split(",")]
    steps = [round(b - a, 9) for a, b in zip(xs, xs[1:])]
    if len(set(steps)) == 1:
        return xs[-1] + steps[0]
    if 0 not in xs and len({round(b / a, 9) for a, b in zip(xs, xs[1:])}) == 1:
        return xs[-1] * xs[-1] / xs[-2]
    if all(c == a + b for a, b, c in zip(xs, xs[1:], xs[2:])):
        return xs[-1] + xs[-2]
    # Otherwise extend the table of differences by one column (exact for polynomial series).
    rows = [xs]
    while len(rows[-1]) > 1 and len(set(rows[-1])) > 1:
        rows.append([b - a for a, b in zip(rows[-1], rows[-1][1:])])
    return sum(row[-1] for row in rows)


@template(r"signed on (\d{1,2} [a-z]+ \d{4}) has an? (\d+)-day settlement period")
def settlement_date(signed, days):
    return parse_date(signed) + timedelta(days=int(days))


# --- Matching an answer to the options ---------------------------------------

def gap(option: str, expected):
    """Distance from an option to the expected answer, and the largest distance
    that still means "equal at the precision the option is printed with"."""
    if isinstance(expected, date):
        return abs((parse_date(option) - expected).days), 0
    digits = re.sub(r"[^\d.,-]", "", option)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return abs(num(digits) - expected), 0.5 * 10 ** -decimals + 1e-9 * max(1, abs(expected))


def answer(question: dict):
    """Return (letter, template name or None, exact) for one row of questions.csv."""
    text = " ".join(question["question_text"].split())
    options = {letter: question[f"option_{letter.lower()}"].strip() for letter in LETTERS}
    for pattern, solve in SOLVERS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            expected = solve(*match.groups())
        except (ValueError, KeyError, ZeroDivisionError):
            break
        gaps = {}
        for letter, option in options.items():
            try:
                gaps[letter] = gap(option, expected)
            except (ValueError, IndexError):
                gaps[letter] = (float("inf"), 0)
        best = min(gaps, key=lambda letter: gaps[letter][0])
        return best, solve.__name__, gaps[best][0] <= gaps[best][1]
    return blind_guess(options), None, False


def blind_guess(options: dict) -> str:
    """Pick for a question no template recognizes. On the 1,200 practice questions the
    second-smallest option is right 54% of the time (a fixed letter: 25%), because
    this quiz's distractors mostly sit one below and two above the answer."""
    try:
        values = {letter: value(option) for letter, option in options.items()}
    except (ValueError, IndexError):
        return "A"
    return sorted(values, key=values.get)[1]


def value(option: str) -> float:
    """An option as a number, or as a day count if it is a date."""
    try:
        return parse_date(option).toordinal()
    except (ValueError, IndexError):
        return num(re.sub(r"[^\d.,-]", "", option))


# --- Input and output ----------------------------------------------------------

def fetch(name: str) -> str:
    """Download a file from the quiz repo as of its newest commit.

    Asking for the file at the exact commit, rather than at `main`, skips the
    few minutes raw.githubusercontent.com caches the branch version for.
    """
    try:
        head = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{QUIZ_REPO}.git", "refs/heads/main"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.split()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        head = "main"
    url = f"https://raw.githubusercontent.com/{QUIZ_REPO}/{head}/{name}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read().decode("utf-8")
        except OSError:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))


def load_csv(name: str, path: str | None) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8") if path else fetch(name)
    return list(csv.DictReader(io.StringIO(text)))


def write_round(round_id: int, answers: dict) -> bool:
    """Write answers/round-NNNN.json; return True if the file is new or changed."""
    path = ANSWERS_DIR / f"round-{round_id:04d}.json"
    body = json.dumps({"round_id": round_id, "answers": answers}, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return False
    path.write_text(body, encoding="utf-8")
    return True


def warn(message: str) -> None:
    # In GitHub Actions this becomes a yellow annotation on the run's summary page.
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: ") + message)


def spans(ids: list[int]) -> str:
    """[1, 2, 3, 7] -> '1-3, 7'"""
    out, start = [], None
    for i, n in enumerate(ids):
        start = n if start is None else start
        if i + 1 == len(ids) or ids[i + 1] != n + 1:
            out.append(str(n) if start == n else f"{start}-{n}")
            start = None
    return ", ".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Answer every released round of the DSO 429 Hourly Quiz.")
    parser.add_argument("--questions", metavar="CSV", help="read a local questions.csv instead of the course repo")
    parser.add_argument("--check", nargs="?", const="", metavar="KEYS_CSV",
                        help="grade practice rounds against practice_keys.csv (fetched unless a path is given)")
    args = parser.parse_args(argv)

    rounds = defaultdict(list)
    for row in load_csv("questions.csv", args.questions):
        rounds[int(row["round_id"])].append(row)

    now = datetime.now(timezone.utc)
    ANSWERS_DIR.mkdir(exist_ok=True)
    picked, written, urgent = {}, [], 0
    for round_id, questions in sorted(rounds.items()):
        answers = {}
        for q in sorted(questions, key=lambda q: int(q["q_index"])):
            letter, kind, exact = answer(q)
            answers[f"q{int(q['q_index'])}"] = letter
            picked[(round_id, int(q["q_index"]))] = (letter, kind)
            if not exact:
                released = datetime.fromisoformat(q["released_at"].strip().replace("Z", "+00:00"))
                if now - released < CORRECTION_WINDOW:
                    urgent += 1
                why = "unrecognized question" if kind is None else f"no option matches {kind} exactly"
                warn(f"round {round_id} q{q['q_index']}: {why}; answered {letter}. {q['question_text']}")
        if write_round(round_id, answers):
            written.append(round_id)

    print(f"{len(rounds)} rounds released; wrote {len(written)}"
          + (f" (rounds {spans(written)})" if written else "") + f" to {ANSWERS_DIR.name}/")

    failed = 0
    if args.check is not None:
        tally = defaultdict(lambda: [0, 0])
        for key in load_csv("practice_keys.csv", args.check or None):
            letter, kind = picked.get((int(key["round_id"]), int(key["q_index"])), (None, None))
            right = letter == key["answer"].strip()
            tally[kind or "unrecognized"][0] += right
            tally[kind or "unrecognized"][1] += 1
            if not right:
                failed += 1
                print(f"  wrong: round {key['round_id']} q{key['q_index']} answered {letter}, key {key['answer']}")
        width = max(map(len, tally))
        for kind, (right, total) in sorted(tally.items()):
            print(f"  {kind:<{width}}  {right:4d}/{total:<4d}")
        total = sum(t for _, t in tally.values())
        print(f"practice keys: {total - failed}/{total} correct")

    return 1 if urgent or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
