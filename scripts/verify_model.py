"""The old speed-to-oil pseudo-verification has been retired.

Use `python -m scripts.benchmark_suite DATASET.json` for explicitly sourced fuel
observations. Oil condition requires independent laboratory/OEM evidence; neither
RPM/800 nor a synthetic smartphone trace supplies that reference.
"""

if __name__=='__main__':
    raise SystemExit(__doc__)
