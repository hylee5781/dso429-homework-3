# DSO 429 Homework 3: The Hourly Quiz

Answers to the [hourly quiz](https://github.com/FaisalXL/dso429-hourly-quiz), one file per round in [`answers/`](answers/), produced and pushed automatically.

## How it works

Every 10 minutes a scheduled GitHub Actions workflow ([`.github/workflows/answer.yml`](.github/workflows/answer.yml)) runs [`solve.py`](solve.py), which:

1. Downloads `questions.csv` at the quiz repo's newest commit, so a new round is seen as soon as it is published rather than after GitHub's raw-file cache expires.
2. Matches each question to one of the 12 question types the quiz uses and computes the answer exactly: compound growth, sale price after a discount, total revenue, currency conversion, blended average order value, gross margin, break-even units, ticket probability, percentage change, ratio split, next value in a series, and settlement date.
3. Picks the option that equals the computed answer at the precision it is printed with, and writes `answers/round-NNNN.json` for every released round.

The workflow then commits and pushes any new or changed files. Every run re-answers every released round, so a round missed during a GitHub delay is picked up by the next run.

No language model is called while the quiz runs. The questions come from fixed templates, so exact arithmetic answers them faster, for free, and without calculation slips.

## Safeguards

- **Checked against the practice keys.** `python3 solve.py --check` grades practice rounds 1–48 against `practice_keys.csv`: 1,200 of 1,200 correct.
- **Fails loudly.** If a question released in the last three hours (while a corrected answer still counts) fits no known type, or no option matches the computed answer exactly, `solve.py` still writes its best guess and then exits with an error. The workflow run fails and GitHub sends an email, leaving time to add the new type before the answer locks.
- **Off-peak schedule.** Runs start at minutes 7, 17, 27, 37, 47 and 57, avoiding the top of the hour, when GitHub's scheduler is busiest and delays runs.

## Running it by hand

```sh
python3 solve.py           # answer every released round
python3 solve.py --check   # also grade the practice rounds against the answer keys
```

Python 3.8+ and its standard library only.
