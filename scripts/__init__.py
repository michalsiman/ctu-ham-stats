# Balíček skriptů – umožňuje `from scripts import qrz_poc` z aplikace
# (denní job v app/okres.py). Skripty jinak fungují i samostatně přes
# `python scripts/<jmeno>.py` (mají vlastní sys.path shim + __main__ guard).
