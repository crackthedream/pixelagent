# Benchmarks

The benchmark set is how we tell whether a milestone actually improved quality, and what each sprite costs.

## Layout
```
benchmarks/
  cases.csv              one row per case (id, input type, reference file or prompt, height, notes)
  refs/                  your reference images (your own art or licensed material)
  ratings.csv            your 1-10 ratings per case and milestone run
  make_synthetic.py      writes synthetic stand-in references for dry runs (refs/synthetic_*.png)
  move_set.py            a character's move set (idle, walk, run, jump, attack, hurt) in parallel, with costs and
                         frame heights (~$0.07 a move)
```

## To fill in before M1
1. Put about 10 character references in `refs/` covering what you will actually make: different views,
   hair/costume complexity, colourful vs muted, at least one with props or weapons.
2. Add about 10 prompt-only cases to `cases.csv` (comprehensive concept descriptions, like you would write).
3. Set the target `height` per case (the sprite height you will use in the game).

## Running
```
python benchmarks/run.py --project bench_runs --dry-run       # M0: plumbing only, no API cost
python benchmarks/run.py --project bench_runs                 # M1+: real models (needs the OpenAI backend)
```
Each run writes `bench_runs/summary.csv` (lint score, critic score, cost, time per case) and a contact
sheet of all finals. Rate each final 1-10 in `ratings.csv`; compare milestones by average rating and cost.
