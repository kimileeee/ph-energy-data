"""Run the whole pipeline: uv run python -m pipeline [--offline]

--offline skips fetch and the live source checks, rebuilding from data/raw and the last data/qa outputs.
"""

import sys

from pipeline import coverage, doe, fetch, geo, publish, report, site, verify

offline = "--offline" in sys.argv
steps = [doe, coverage, geo, publish, report, site] if offline else [fetch, doe, coverage, geo, verify, publish, report, site]
for step in steps:
    print(f"== {step.__name__}", file=sys.stderr)
    step.main()
