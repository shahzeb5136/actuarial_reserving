"""Actuarial reserving engine for the Medical LOB Reserving Tool.

Pure Python / numpy / pandas — no Streamlit imports — so every calculation can
be unit-tested and compared against the original R implementation (app.R,
which used the R ChainLadder package).
"""

__version__ = "1.0.0"
