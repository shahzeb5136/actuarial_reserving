# ============================================================================
#  Realistic dummy claims generator for the Reserving Tool
# ----------------------------------------------------------------------------
#  Unlike a naive generator (independent random origin & payment dates, which
#  produces a meaningless triangle), this builds claims with:
#    * a genuine payment development lag (most paid early, a tail that runs off)
#    * origin-month SEASONALITY (winter peak — typical for medical LOB)
#    * three subcategories with different volumes & speed of settlement
#  Output: aggregated_claims.xlsx  (OriginDate, PaymentDate, ClaimAmount, SubCat)
# ============================================================================

suppressPackageStartupMessages({
  library(dplyr); library(lubridate); library(writexl)
})

set.seed(42)

origin_start <- as.Date("2021-01-01")
origin_end   <- as.Date("2023-12-31")
n_claims     <- 80000

# Subcategory profiles: volume weight, mean severity, settlement speed (mean lag months)
subcats <- tibble(
  SubCat       = c("A", "B", "C"),
  weight       = c(0.50, 0.30, 0.20),
  mean_sev     = c(15000, 28000, 9000),
  mean_lag_mo  = c(3, 6, 2)        # B settles slowest -> longer tail
)

# Monthly seasonality multiplier on claim INCIDENCE (origin month)
# Higher in Dec-Feb, lower in summer.
seasonal_mult <- c(1.30, 1.25, 1.05, 0.95, 0.90, 0.85,
                   0.85, 0.90, 0.95, 1.05, 1.15, 1.30)

# Draw origin dates weighted by seasonality
all_days <- seq(origin_start, origin_end, by = "day")
day_w    <- seasonal_mult[month(all_days)]
origin   <- sample(all_days, n_claims, replace = TRUE, prob = day_w)

# Assign subcategory
sc <- sample(subcats$SubCat, n_claims, replace = TRUE, prob = subcats$weight)
prof <- subcats[match(sc, subcats$SubCat), ]

# Development lag (months) ~ Exponential-ish, by subcat; floor at 0
lag_mo  <- round(rexp(n_claims, rate = 1 / prof$mean_lag_mo))
payment <- origin %m+% months(lag_mo)

# Cap payments at the latest observable date (mimics a real valuation cut-off)
valuation_date <- origin_end
payment <- pmin(payment, valuation_date)

# Severity ~ Gamma, by subcat
amount <- rgamma(n_claims, shape = 2, scale = prof$mean_sev / 2)

raw <- tibble(
  OriginDate  = as.Date(origin),
  PaymentDate = as.Date(payment),
  ClaimAmount = round(amount, 2),
  SubCat      = sc
)

aggregated <- raw %>%
  group_by(OriginDate, PaymentDate, SubCat) %>%
  summarise(ClaimAmount = sum(ClaimAmount), .groups = "drop")

cat("Rows (aggregated):", nrow(aggregated), "\n")
print(head(aggregated))

write_xlsx(aggregated, "aggregated_claims.xlsx")
cat("Saved aggregated_claims.xlsx\n")

# ----------------------------------------------------------------------------
#  Companion exposure / earned premium table (monthly origin periods)
# ----------------------------------------------------------------------------
#  Earned premium and exposure are built to be broadly consistent with the
#  claims: exposure follows the same winter-peaked seasonality as incidence,
#  premium is exposure priced at a notional rate with a small upward trend, so
#  the implied loss ratio lands in a sensible range. Period is written as a
#  month-start date the reserving app can parse and align.
exp_months <- seq(origin_start, origin_end, by = "month")

exposure_tbl <- tibble(
  Period = exp_months,
  mnum   = month(exp_months),
  ynum   = year(exp_months)
) %>%
  mutate(
    seas_e   = seasonal_mult[mnum],                  # same shape as incidence
    trend    = 1 + 0.004 * (interval(origin_start, Period) %/% months(1)),
    Exposure = round(1000 * seas_e * trend),         # earned exposure units
    RatePerUnit = 600,                               # notional premium rate
    EarnedPremium = round(Exposure * RatePerUnit * (1 + runif(n(), -0.03, 0.03)))
  ) %>%
  select(Period, EarnedPremium, Exposure)

write_xlsx(exposure_tbl, "exposure_premium.xlsx")
cat("Saved exposure_premium.xlsx (", nrow(exposure_tbl), "monthly periods )\n")
