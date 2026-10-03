# Medical LOB Reserving Tool — Streamlit edition

A Python / Streamlit port of the R Shiny app in `archive/r_shiny_app/app.R` (archived): Mack Chain Ladder,
ODP bootstrap, Bornhuetter-Ferguson / Benktander / Cape Cod, a
credibility-weighted blend, actuary adjustments with an audit trail, a recast
back-test, seasonality analysis, an executive summary and the Excel export —
same 14 tabs, same sidebar controls, same explanatory notes.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Run from this folder so Streamlit picks up `.streamlit/config.toml`
(theme, 60 MB upload limit). Upload a claims workbook (`OriginDate`,
`PaymentDate`, `ClaimAmount`, `SubCat`) and optionally an exposure workbook
(`Period`, `EarnedPremium`, `Exposure`), or press **Use the bundled sample …
file** to use the data in `sample_data/` (the same files the R app used).

`python generate_dummy_data.py [out_dir]` regenerates dummy data (a port of
`archive/r_shiny_app/generate_dummy_data.R`; different random numbers, same structure).

## Layout

| Path | What |
|---|---|
| `app.py` | Entry point: sidebar controls, run button, download, tabs |
| `views/` | One function per tab (`data_tabs`, `model_tabs`, `adjust_tabs`, `analysis_tabs`) |
| `ui/` | Charts (Plotly), cards / explainer blocks / tables, session state |
| `reserving/` | The calculation engine — pure Python, no Streamlit |
| `reserving/mack.py` | Mack chain ladder, ported line by line from R ChainLadder 0.2.21 |
| `reserving/bootstrap.py` | ODP bootstrap (ChainLadder `BootChainLadder`), vectorised |
| `reserving/methods.py` | Exposure a-priori, BF, Benktander, Cape Cod, the blend |
| `reserving/pipeline.py` | Runs everything for one set of settings (the Shiny `analysis_results`) |
| `reserving/adjustments.py`, `summary.py`, `export.py` | Worksheet logic, narrative text, Excel workbook |
| `tests/` | Parity tests against the R app (below) |

## Parity with the R app

`tests/r_reference/make_reference.R` drives the **real** `app.R` server
headlessly (`shiny::testServer`): it uploads the files, sets the inputs,
presses Run analysis, edits the adjustments worksheet, fills in the sign-off
and saves the app's own Excel download, rendered summaries, notifications and
model internals for 20 scenarios (monthly / quarterly, every sub-category,
with / without exposure in several file layouts, tail factors, every prior,
both process distributions, hold-backs up to 6).

```bash
pip install -r requirements-dev.txt
Rscript tests/r_reference/make_reference.R    # optional: refresh the references (needs R + the app's packages)
python -m pytest tests                        # compares Python against them
```

Every cell of every Excel sheet, the Executive / Final Summary text word for
word, value boxes, notifications, worksheet state and Mack internals match R
to floating-point precision (largest relative difference ≈ 6e-11).
`tests/parity_report.md` is written on each run.

The bootstrap is random (and the R app sets no seed), so R's own simulated
draws are injected for those comparisons. The simulation engine is checked
separately: fed R's residual resample it reproduces R's simulated reserves
exactly, and its distribution matches R's (mean, SD, quantiles, KS test).

### Intentional differences from the R app

1. **Total Mack S.E.** — R shows NA in the Total row (and "n/a" in the
   executive summary) because it looks up `"Mack.S.E"` while `summary()` names
   the row `"Mack S.E.:"`. Python shows the value R computed
   (`Total.Mack.S.E`).
2. **Development lags with no payments** — R's `pivot_wider` drops a lag
   column nobody was paid in (monthly SubCat A has none at lag 28, SubCat C at
   lag 19), shifting later payments into the wrong development period. Python
   builds the complete lag grid (and origin grid). Those scenarios match R
   exactly once R gets the same one-line fix.
3. **Mack residual plot** — R's "Residual diagnostics" card actually drew the
   development-curve plot; Python shows the standardised residuals the card
   describes, and the development curves in their own card.
4. **Exposure `Period` parsing** — all the formats R understood parse
   identically; inputs R mangled (`Jan-21` → year 0021, `01/02/2021` → year
   0001) or rejected the whole file over (`Sept 2022`) are handled sensibly.
5. Degenerate triangles where R's ChainLadder would stop with an error (a zero
   cumulative in the fitted region, too few links to extrapolate sigma,
   over-dispersion below 1 for the ODP draw) get a sensible fallback instead.

### Additions beyond the R app

- **Booked ultimate — paid to date vs IBNR** (Actuary Adjustments and Final
  Summary tabs): per-origin stacked bars with the share not yet paid in a
  panel below, plus a roll-up by origin year. Built from the same booked
  position as the rest of the page (`reserving.summary.ultimate_split`).

The bundled dummy exposure file (from the R generator) has premiums far below
claims, so its implied loss ratios are in the thousands of percent; the
methods still work, but real premium data will give realistic ratios.
