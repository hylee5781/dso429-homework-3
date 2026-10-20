# DSO 429 Homework 3: The Hourly Quiz

Answers to the [hourly quiz](https://github.com/FaisalXL/dso429-hourly-quiz), one file per round in [`answers/`](answers/), produced and pushed automatically.

## How it works

Every 5 minutes a GitHub Actions workflow ([`.github/workflows/answer.yml`](.github/workflows/answer.yml)) runs [`solve.py`](solve.py), which:

1. Downloads `questions.csv` at the quiz repo's newest commit, so a new round is seen as soon as it is published rather than after GitHub's raw-file cache expires.
2. Matches each question to one of the 12 question types the quiz uses and computes the answer exactly: compound growth, sale price after a discount, total revenue, currency conversion, blended average order value, gross margin, break-even units, ticket probability, percentage change, ratio split, next value in a series, and settlement date.
3. Picks the option that equals the computed answer at the precision it is printed with, and writes `answers/round-NNNN.json` for every released round.

The workflow then commits and pushes any new or changed files. Every pass re-answers every released round, so a round missed during a GitHub delay is picked up by the next pass.

Each workflow run keeps polling for 5.5 hours and then starts the next run itself, so polling never stops. GitHub's cron scheduler is only a backup: from October 17 to 19 it fired 18 of about 400 scheduled runs, hours apart, which pushed several rounds past the 3-hour full-credit window. If the chain of runs ever breaks, the next cron run that fires restarts it. Runs stop starting new ones on October 24, after the quiz ends.

No language model is called while the quiz runs. The questions come from fixed templates, so exact arithmetic answers them faster, for free, and without calculation slips.

## Safeguards

- **Checked against the practice keys.** `python3 solve.py --check` grades practice rounds 1–48 against `practice_keys.csv`: 1,200 of 1,200 correct.
- **Fails loudly.** If a question released in the last three hours (while a corrected answer still counts) fits no known type, or no option matches the computed answer exactly, `solve.py` still writes its best guess and then exits with an error. The workflow opens an issue in this repository, which GitHub emails to the owner, and does the same after 30 minutes of failed downloads. Each pass starts from the newest `main`, so a fix pushed to `solve.py` is used within 5 minutes, before the answer locks.
- **Stopping it.** Cancelling a run stops the chain, but a cron run can restart it. To stop it for good, disable the workflow: `gh workflow disable answer.yml`.

## Running it by hand

```sh
python3 solve.py           # answer every released round
python3 solve.py --check   # also grade the practice rounds against the answer keys
```

Python 3.8+ and its standard library only.
