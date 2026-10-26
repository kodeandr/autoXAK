"""Synthetic oil calibration is no longer supported.

Fitting one invented formula to another does not calibrate a real vehicle.
Use `python -m scripts.benchmark_suite DATASET.json` for independently sourced
fuel observations. Engine maps and oil models need actual calibration evidence.
"""

if __name__=='__main__':
    raise SystemExit(__doc__)
