"""The app's tabs, in the same order and with the same labels as the R app."""
from views import adjust_tabs, analysis_tabs, data_tabs, model_tabs

TABS = [
    ("1. Data & Checks", data_tabs.data_checks),
    ("1b. Exposure & Premium", data_tabs.exposure),
    ("2. Cumulative Triangle", data_tabs.cumulative),
    ("3. Incremental Triangle", data_tabs.incremental),
    ("4. Development Factors", data_tabs.dev_factors),
    ("5. Reserve Estimates", model_tabs.reserve_estimates),
    ("6. Bootstrap Distribution", model_tabs.bootstrap),
    ("7. Mack Diagnostics", model_tabs.mack_diagnostics),
    ("8. Blended Estimate", model_tabs.blended),
    ("9. Actuary Adjustments", adjust_tabs.adjustments),
    ("10. Final Summary", adjust_tabs.final_summary),
    ("11. Recast Analysis", analysis_tabs.recast),
    ("12. Seasonality", analysis_tabs.seasonality),
    ("13. Executive Summary", analysis_tabs.executive_summary),
]
