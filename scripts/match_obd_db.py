"""The old oil_percent * 250 pseudo-reference has been retired.

Export original trip observations without modifying production balances. Validate
paired fuel observations with `python -m scripts.benchmark_suite DATASET.json`.
Measured engine time and oil equivalent wear are different physical quantities.
"""

if __name__=='__main__':
    raise SystemExit(__doc__)
