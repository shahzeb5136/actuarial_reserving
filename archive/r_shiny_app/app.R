# ============================================================================
#  Medical LOB Actuarial Reserving Tool
#  Mack Chain Ladder + Bootstrap + Credibility-blended estimate
#  Recast (back-test) analysis  |  Seasonality analysis
# ----------------------------------------------------------------------------
#  Expected input: .xlsx with columns
#     OriginDate, PaymentDate, ClaimAmount, SubCat   (long, transactional or
#     aggregated). The app aggregates internally, so duplicate
#     Origin/Payment/SubCat rows are summed rather than rejected.
# ============================================================================

# ---- Packages --------------------------------------------------------------
# install.packages(c("shiny","bslib","readxl","dplyr","lubridate","tidyr",
#   "ChainLadder","DT","writexl","ggplot2","scales","tibble","purrr"))

suppressPackageStartupMessages({
  library(shiny)
  library(bslib)
  library(readxl)
  library(dplyr)
  library(lubridate)
  library(tidyr)
  library(tibble)
  library(purrr)
  library(ChainLadder)
  library(DT)
  library(writexl)
  library(ggplot2)
  library(scales)
})

options(shiny.maxRequestSize = 60 * 1024^2)
theme_set(theme_minimal(base_size = 13))

# ---- Explanatory text helper ----------------------------------------------
# Renders a two-part explainer beneath each plot / graph / table:
#   * "For an executive" — plain-language, what-it-means-for-the-business
#   * "Technical note"   — the actuarial/statistical detail
explain_block <- function(exec, technical) {
  div(
    class = "mt-2",
    div(class = "p-2 mb-1",
        style = "background:#EAF2F8; border-left:4px solid #2C3E50; border-radius:4px;",
        tags$span(style = "font-weight:600; color:#2C3E50;", "For an executive: "),
        tags$span(exec)),
    div(class = "p-2",
        style = "background:#F4F6F7; border-left:4px solid #95A5A6; border-radius:4px; font-size:0.9em;",
        tags$span(style = "font-weight:600; color:#566573;", "Technical note: "),
        tags$span(technical))
  )
}

# ============================================================================
#  HELPER FUNCTIONS  (pure, testable, no Shiny dependency)
# ============================================================================

# Aggregate raw claims into a tidy long triangle frame (origin x dev, monthly
# OR quarterly granularity). Returns incremental claims per origin/dev cell.
build_long_triangle <- function(df, period = c("month", "quarter")) {
  period <- match.arg(period)
  step   <- if (period == "month") months(1) else months(3)

  df %>%
    mutate(
      OriginPeriod = floor_date(OriginDate,  period),
      PayPeriod    = floor_date(PaymentDate, period),
      DevLag       = interval(OriginPeriod, PayPeriod) %/% step + 1
    ) %>%
    filter(DevLag >= 1) %>%
    group_by(OriginPeriod, DevLag) %>%
    summarise(Incremental = sum(ClaimAmount, na.rm = TRUE), .groups = "drop") %>%
    arrange(OriginPeriod, DevLag)
}

# Long -> wide incremental matrix (origins as rownames, Dev_n columns)
long_to_incr_matrix <- function(long_df) {
  wide <- long_df %>%
    pivot_wider(names_from = DevLag, values_from = Incremental,
                names_prefix = "Dev_", names_sort = TRUE)
  origins <- wide$OriginPeriod
  m <- as.matrix(select(wide, -OriginPeriod))
  rownames(m) <- as.character(origins)
  m[is.na(m)] <- NA          # keep NA for upper-right (future) cells
  m
}

# Incremental matrix -> cumulative triangle (respecting the run-off shape:
# only cells on/under the leading diagonal are populated).
incr_to_cum <- function(incr_m) {
  n_org <- nrow(incr_m); n_dev <- ncol(incr_m)
  cum <- matrix(NA_real_, n_org, n_dev,
                dimnames = dimnames(incr_m))
  for (i in seq_len(n_org)) {
    # number of observed dev periods for this origin (run-off diagonal)
    avail <- n_org - i + 1
    avail <- min(avail, n_dev)
    vals  <- incr_m[i, seq_len(avail)]
    vals[is.na(vals)] <- 0
    cum[i, seq_len(avail)] <- cumsum(vals)
  }
  cum
}

# Pretty currency / number formatting for DT
fmt_dt_numeric <- function(dt, cols, digits = 0) {
  cols <- cols[cols %in% names(dt$x$data)]
  if (length(cols)) dt <- formatRound(dt, columns = cols, digits = digits)
  dt
}

# Null / empty coalesce (used for optional sign-off inputs)
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

# Safe percentage of development
dev_to_date <- function(latest, ultimate) {
  ifelse(ultimate == 0, NA_real_, latest / ultimate)
}

# ============================================================================
#  RESERVING ENGINE
# ============================================================================

# Run Mack on a cumulative triangle, return tidy by-origin + totals frame.
run_mack <- function(cum_tri, tail = 1.0) {
  mack <- MackChainLadder(Triangle = cum_tri, est.sigma = "Mack", tail = tail)
  s    <- summary(mack)

  by_origin <- as.data.frame(s$ByOrigin) %>%
    rownames_to_column("Origin") %>%
    transmute(
      Origin,
      Latest      = .data[["Latest"]],
      Dev.To.Date = .data[["Dev.To.Date"]],
      Ultimate    = .data[["Ultimate"]],
      IBNR        = .data[["IBNR"]],
      Mack.S.E    = .data[["Mack.S.E"]],
      CV          = .data[["CV(IBNR)"]]
    )

  tot <- s$Totals
  totals <- tibble(
    Origin      = "Total",
    Latest      = tot["Latest", 1],
    Dev.To.Date = NA_real_,
    Ultimate    = tot["Ultimate", 1],
    IBNR        = tot["IBNR", 1],
    Mack.S.E    = tot["Mack.S.E", 1],
    CV          = tot["CV(IBNR)", 1]
  )

  list(
    model      = mack,
    by_origin  = bind_rows(by_origin, totals),
    dev_factors = mack$f,                       # selected development factors
    full_tri   = as.data.frame(mack$FullTriangle) %>%
                   rownames_to_column("Origin")
  )
}

# Bootstrap (ODP) chain ladder -> predictive distribution of total reserve.
# Returns quantile table + per-origin mean/SE + simulated totals vector.
run_bootstrap <- function(cum_tri, n_sims = 1000,
                          process_dist = c("gamma", "od.pois")) {
  process_dist <- match.arg(process_dist)
  bs <- BootChainLadder(Triangle = cum_tri, R = n_sims,
                        process.distr = process_dist)
  s  <- summary(bs)

  by_origin <- as.data.frame(s$ByOrigin) %>%
    rownames_to_column("Origin")

  sim_totals <- bs$IBNR.Totals
  qtab <- tibble(
    Quantile = c("Mean", "SD", "5%", "25%", "50%", "75%", "95%", "99%", "99.5%"),
    IBNR = c(mean(sim_totals), sd(sim_totals),
             quantile(sim_totals, c(.05, .25, .50, .75, .95, .99, .995)))
  )

  list(model = bs, by_origin = by_origin,
       quantiles = qtab, sim_totals = sim_totals)
}

# ----------------------------------------------------------------------------
# EXPOSURE-BASED A-PRIORI
# ----------------------------------------------------------------------------
# Convert an exposure base (earned premium, or a count/exposure measure) keyed
# by origin period into a per-origin a-priori expected ULTIMATE, using a single
# data-derived a-priori loss ratio (ELR).
#
# The ELR is estimated on a DEVELOPED basis (the "Cape Cod" / Stanard-Buhlmann
# estimator): only the share of each origin's exposure that has actually emerged
# (exposure * %developed) is used as the denominator, so green periods do not
# dilute the ratio. This is the standard way to derive an exposure a-priori
# directly from the triangle when no external plan loss ratio is given:
#
#       ELR = sum(latest) / sum(exposure * %developed)
#       apriori_ultimate_i = ELR * exposure_i
#
# `base_vec` is the exposure measure aligned to the rows of cum_tri (NA allowed;
# rows with NA/<=0 exposure fall back to the grossed-up latest so the method
# still returns a finite number for them). Returns a list with the per-origin
# a-priori ultimate vector and the fitted ELR.
exposure_apriori <- function(cum_tri, dev_factors, base_vec) {
  n_dev    <- ncol(cum_tri)
  f        <- dev_factors
  latest   <- apply(cum_tri, 1, function(r) tail(r[!is.na(r)], 1))
  last_pos <- apply(cum_tri, 1, function(r) sum(!is.na(r)))
  pct_dev  <- vapply(seq_along(last_pos), function(i) {
    pos <- last_pos[i]; if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  base_vec <- as.numeric(base_vec)
  valid    <- is.finite(base_vec) & base_vec > 0

  # Developed-basis ELR over origins with usable exposure.
  denom <- sum(base_vec[valid] * pct_dev[valid], na.rm = TRUE)
  elr   <- if (is.finite(denom) && denom > 0)
             sum(latest[valid], na.rm = TRUE) / denom else NA_real_

  apriori <- rep(NA_real_, length(latest))
  if (is.finite(elr)) apriori[valid] <- elr * base_vec[valid]

  # Fallback for rows without usable exposure: grossed-up latest (latest/%dev).
  fill <- !valid | !is.finite(apriori)
  if (any(fill)) {
    grossed <- ifelse(pct_dev > 0, latest / pct_dev, NA_real_)
    apriori[fill] <- grossed[fill]
  }

  list(apriori = apriori, elr = elr, pct_dev = pct_dev,
       latest = latest, base = base_vec)
}

# Bornhuetter-Ferguson using an a-priori loss ratio applied to an exposure base.
# When no exposure supplied, uses CL ultimate as the a-priori expectation
# (i.e. a "CL-seeded BF" -> Benktander-style sanity check).
run_bf <- function(cum_tri, apriori_ultimate) {
  cl  <- MackChainLadder(cum_tri, est.sigma = "Mack")
  # development pattern from CL cumulative factors
  f   <- cl$f
  f_to_ult <- rev(cumprod(rev(f)))            # factors to ultimate by dev col
  n_dev <- ncol(cum_tri)
  # % reported per origin = latest cumulative / (latest * factor-to-ult)
  latest <- apply(cl$Triangle, 1, function(r) tail(r[!is.na(r)], 1))
  # last observed dev position per origin
  last_pos <- apply(cl$Triangle, 1, function(r) sum(!is.na(r)))
  pct_dev  <- vapply(seq_along(last_pos), function(i) {
    pos <- last_pos[i]
    if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  ultimate_bf <- latest + apriori_ultimate * (1 - pct_dev)

  tibble(
    Origin    = rownames(cum_tri),
    Latest    = latest,
    Pct.Dev   = pct_dev,
    Apriori   = apriori_ultimate,
    Ultimate  = ultimate_bf,
    IBNR      = ultimate_bf - latest
  )
}

# ============================================================================
#  BLENDED / CREDIBILITY-WEIGHTED ESTIMATE
# ----------------------------------------------------------------------------
#  WHY THIS EXISTS
#  Pure Chain Ladder (CL) projects each origin period by multiplying its
#  latest observed cumulative by the product of remaining development factors.
#  For mature origin periods (almost fully developed) this is stable and good.
#  For the YOUNGEST periods - especially the latest diagonal / lag-1 cell, and
#  especially on monthly triangles - the latest cell is tiny, the factor
#  product it is multiplied by is huge, and any noise (one large claim, a slow
#  payment month, a seasonal trough) is amplified into a wildly volatile
#  ultimate. The estimate stops "making sense".
#
#  THE FIX (classic actuarial credibility blending)
#  Anchor the immature periods to a STABLE PRIOR and let their own data earn
#  weight only as they mature. We compute several estimators that each trade
#  off data-responsiveness vs stability, then blend them by maturity:
#
#    * Chain Ladder (CL)        - all weight on own data (good when mature)
#    * Expected / Seasonal      - a stable prior built from the historical
#                                 average emergence per period, seasonally
#                                 adjusted (good when green)
#    * Bornhuetter-Ferguson     - latest + prior * (1 - %dev); replaces the
#                                 leveraged tail with the prior
#    * Benktander (iterated BF) - BF re-seeded with its own ultimate; a
#                                 credibility-optimal midpoint of CL & BF
#    * Cape Cod                 - prior loss level derived from the data,
#                                 maturity-weighted across origins
#
#  The recommended BLEND uses a maturity credibility Z = (%developed)^kappa:
#       Ultimate_blend = Z * CL + (1 - Z) * Prior_BF_family
#  Z -> 1 for old periods (pure CL); Z -> small for the newest (mostly prior).
#  kappa lets the user tune how aggressively to discount green periods.
# ============================================================================

# Build a stable per-origin a-priori expected ULTIMATE that does not depend on
# leveraging the thin latest diagonal.
#   * If an exposure base (earned premium) is supplied, the prior is a proper
#     exposure-based expectation: a developed-basis loss ratio applied to each
#     origin's own earned premium. Premium already carries the volume/level and
#     period-to-period differences, so the seasonal tilt is NOT re-applied (that
#     would double-count). This is the recommended actuarial BF/Benktander prior.
#   * If no exposure is supplied, it falls back to the original behaviour: a
#     typical fully-developed LEVEL estimated from the mature periods, carried to
#     the green periods and optionally tilted by the seasonal index.
build_expected_prior <- function(cum_tri, dev_factors, seas_index = NULL,
                                 period = "quarter", origin_dates = NULL,
                                 exposure = NULL) {
  n_org <- nrow(cum_tri); n_dev <- ncol(cum_tri)
  f     <- dev_factors

  latest   <- apply(cum_tri, 1, function(r) tail(r[!is.na(r)], 1))
  last_pos <- apply(cum_tri, 1, function(r) sum(!is.na(r)))

  # % developed for each origin from the CL pattern (same definition as BF)
  pct_dev <- vapply(seq_len(n_org), function(i) {
    pos <- last_pos[i]
    if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  has_exp <- !is.null(exposure) &&
             any(is.finite(as.numeric(exposure)) & as.numeric(exposure) > 0)

  if (has_exp) {
    # Exposure-based prior: developed loss ratio * earned premium per origin.
    ea               <- exposure_apriori(cum_tri, f, exposure)
    apriori_ultimate <- ea$apriori
    return(tibble(
      Origin    = rownames(cum_tri),
      Latest    = latest,
      Pct.Dev   = pct_dev,
      Seas.Mult = 1,                 # premium carries the level; no extra tilt
      Exposure  = ea$base,
      ELR       = ea$elr,
      Apriori   = apriori_ultimate
    ))
  }

  # ---- No-exposure fallback (original level + seasonal-tilt logic) -----------
  # A naive grossed-up ultimate per origin (latest / %dev). For mature periods
  # this is reliable; for green periods it is the very thing that is volatile -
  # so we DO NOT use it directly for the prior. Instead we use the mature
  # periods to estimate a typical ultimate LEVEL, then carry that level to the
  # green periods (optionally seasonally adjusted).
  grossed <- ifelse(pct_dev > 0, latest / pct_dev, NA_real_)

  # "Mature" = at least ~70% developed; these anchor the level.
  mature <- pct_dev >= 0.70
  if (sum(mature) < 1) mature <- pct_dev >= median(pct_dev, na.rm = TRUE)

  base_level <- mean(grossed[mature], na.rm = TRUE)
  if (!is.finite(base_level)) base_level <- mean(grossed, na.rm = TRUE)

  # Seasonal tilt (optional). seas_index is a named numeric vector keyed by the
  # month abbreviation (Jan..Dec), index 100 = average. We convert each origin
  # period's month to a multiplier around 1.0. For quarters we average the
  # three constituent months.
  seas_mult <- rep(1, n_org)
  if (!is.null(seas_index) && !is.null(origin_dates)) {
    idx <- seas_index / 100
    seas_mult <- vapply(seq_len(n_org), function(i) {
      d <- origin_dates[i]
      if (is.na(d)) return(1)
      if (period == "month") {
        m <- as.integer(format(d, "%m"))
        val <- idx[m]; if (length(val) == 0 || is.na(val)) 1 else val
      } else {
        q  <- (as.integer(format(d, "%m")) - 1) %/% 3
        ms <- (q * 3 + 1):(q * 3 + 3)
        val <- mean(idx[ms], na.rm = TRUE); if (is.na(val)) 1 else val
      }
    }, numeric(1))
  }

  apriori_ultimate <- base_level * seas_mult

  tibble(
    Origin   = rownames(cum_tri),
    Latest   = latest,
    Pct.Dev  = pct_dev,
    Seas.Mult = seas_mult,
    Exposure  = NA_real_,
    ELR       = NA_real_,
    Apriori  = apriori_ultimate
  )
}

# Benktander / iterated BF. n_iter = 1 gives plain BF, n_iter -> inf -> CL.
# n_iter = 2 is the classic Benktander (Gunnar Benktander, 1976).
run_benktander <- function(cum_tri, dev_factors, apriori_ultimate, n_iter = 2) {
  n_dev <- ncol(cum_tri)
  f     <- dev_factors
  latest   <- apply(cum_tri, 1, function(r) tail(r[!is.na(r)], 1))
  last_pos <- apply(cum_tri, 1, function(r) sum(!is.na(r)))
  pct_dev  <- vapply(seq_along(last_pos), function(i) {
    pos <- last_pos[i]; if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  U <- apriori_ultimate
  for (k in seq_len(n_iter)) {
    U <- latest + U * (1 - pct_dev)     # BF recursion, re-seeding U each pass
  }
  tibble(Origin = rownames(cum_tri), Latest = latest,
         Pct.Dev = pct_dev, Ultimate = U, IBNR = U - latest)
}

# Cape Cod (Stanard-Buhlmann): a single data-derived expected loss LEVEL,
# maturity weighted across origins.
#   * With an exposure base supplied, the level is a proper loss ratio applied
#     to each origin's own exposure:
#         ELR = sum(latest) / sum(exposure * %developed)
#         apriori_i = ELR * exposure_i,  ultimate_i = latest_i + apriori_i*(1-%dev_i)
#     This is the textbook exposure-based Cape Cod.
#   * With no exposure (exposure = NULL), it degrades to the equal-exposure
#     assumption (every origin carries the same expected ultimate level):
#         level = sum(latest) / sum(%developed)
run_capecod <- function(cum_tri, dev_factors, exposure = NULL) {
  n_dev <- ncol(cum_tri)
  f     <- dev_factors
  latest   <- apply(cum_tri, 1, function(r) tail(r[!is.na(r)], 1))
  last_pos <- apply(cum_tri, 1, function(r) sum(!is.na(r)))
  pct_dev  <- vapply(seq_along(last_pos), function(i) {
    pos <- last_pos[i]; if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  has_exp <- !is.null(exposure) &&
             any(is.finite(as.numeric(exposure)) & as.numeric(exposure) > 0)

  if (has_exp) {
    ea       <- exposure_apriori(cum_tri, f, exposure)
    apriori  <- ea$apriori                 # per-origin expected ultimate
    ult      <- latest + apriori * (1 - pct_dev)
    tibble(Origin = rownames(cum_tri), Latest = latest, Pct.Dev = pct_dev,
           Exposure = ea$base, ELR = ea$elr,
           Apriori = apriori, Ultimate = ult, IBNR = ult - latest)
  } else {
    # Equal-exposure fallback (original behaviour).
    elr_level <- sum(latest) / sum(pct_dev)
    ult       <- latest + elr_level * (1 - pct_dev)
    tibble(Origin = rownames(cum_tri), Latest = latest, Pct.Dev = pct_dev,
           Exposure = NA_real_, ELR = NA_real_,
           Apriori = elr_level, Ultimate = ult, IBNR = ult - latest)
  }
}

# Master blend. Pulls CL, the seasonal/expected prior, BF, Benktander, Cape Cod
# together and produces a maturity-credibility-weighted recommended ultimate.
#   kappa  : credibility exponent. Z = pct_dev^kappa.
#            kappa = 1 -> Z linear in %dev; kappa < 1 trusts data sooner;
#            kappa > 1 discounts green periods harder.
#   prior  : which prior the blend leans on for the (1 - Z) part:
#            "bf", "benktander", or "capecod".
#   earned_premium : optional per-origin earned premium (aligned to cum_tri
#            rows). When supplied, the BF / Benktander a-priori is an exposure
#            based developed-loss-ratio * earned premium.
#   exposure : optional per-origin exposure measure. When supplied, Cape Cod
#            uses the proper exposure-weighted ELR. Defaults to earned_premium
#            if exposure is not separately given.
run_blend <- function(cum_tri, mack_obj, seas_index = NULL,
                      period = "quarter", origin_dates = NULL,
                      kappa = 1.5, prior = c("benktander", "bf", "capecod"),
                      earned_premium = NULL, exposure = NULL) {
  prior <- match.arg(prior)
  f     <- mack_obj$f

  # Chain Ladder ultimates straight from the fitted Mack object.
  cl_ult <- apply(mack_obj$FullTriangle, 1, function(r) tail(r[!is.na(r)], 1))
  latest <- apply(cum_tri, 1, function(r) tail(r[!is.na(r)], 1))
  last_pos <- apply(cum_tri, 1, function(r) sum(!is.na(r)))
  n_dev <- ncol(cum_tri)
  pct_dev <- vapply(seq_along(last_pos), function(i) {
    pos <- last_pos[i]; if (pos >= n_dev) return(1)
    1 / prod(f[pos:(n_dev - 1)])
  }, numeric(1))

  # Exposure-based a-priori (BF/Benktander on earned premium, Cape Cod on
  # exposure). Cape Cod falls back to earned premium if no separate exposure.
  cc_exposure <- if (!is.null(exposure)) exposure else earned_premium

  prior_tbl <- build_expected_prior(cum_tri, f, seas_index, period,
                                    origin_dates, exposure = earned_premium)
  apriori   <- prior_tbl$Apriori

  bf   <- run_bf(cum_tri, apriori)
  benk <- run_benktander(cum_tri, f, apriori, n_iter = 2)
  cc   <- run_capecod(cum_tri, f, exposure = cc_exposure)

  prior_ult <- switch(prior,
                      bf         = bf$Ultimate,
                      benktander = benk$Ultimate,
                      capecod    = cc$Ultimate)

  # Maturity credibility. Clamp to [0,1].
  Z <- pmin(pmax(pct_dev, 0), 1)^kappa
  blend_ult <- Z * cl_ult + (1 - Z) * prior_ult

  # Exposure / premium for display (aligned to rows; NA where not supplied).
  ep_vec  <- if (!is.null(earned_premium)) as.numeric(earned_premium)
             else rep(NA_real_, length(latest))
  exp_vec <- if (!is.null(cc_exposure)) as.numeric(cc_exposure)
             else rep(NA_real_, length(latest))

  detail <- tibble(
    Origin     = rownames(cum_tri),
    Latest     = latest,
    Pct.Dev    = pct_dev,
    Credibility = Z,
    EarnedPrem  = ep_vec,
    Exposure    = exp_vec,
    CL          = cl_ult,
    Expected    = apriori,
    BF          = bf$Ultimate,
    Benktander  = benk$Ultimate,
    CapeCod     = cc$Ultimate,
    Blended     = blend_ult,
    IBNR.CL     = cl_ult   - latest,
    IBNR.Blend  = blend_ult - latest
  )

  # Fitted a-priori loss ratios (developed basis) for transparency in the UI.
  elr_premium <- if (!is.null(earned_premium))
                   exposure_apriori(cum_tri, f, earned_premium)$elr else NA_real_
  elr_capecod <- if ("ELR" %in% names(cc)) cc$ELR[1] else NA_real_
  has_exposure <- !is.null(earned_premium) || !is.null(exposure)

  # Long form for plotting method curves across origins.
  methods_long <- detail %>%
    select(Origin, Pct.Dev, CL, Expected, BF, Benktander, CapeCod, Blended) %>%
    pivot_longer(c(CL, Expected, BF, Benktander, CapeCod, Blended),
                 names_to = "Method", values_to = "Ultimate")

  totals <- tibble(
    Method = c("Chain Ladder", "Expected/Seasonal", "Bornhuetter-Ferguson",
               "Benktander", "Cape Cod", "Blended (recommended)"),
    Total.Ultimate = c(sum(detail$CL), sum(detail$Expected), sum(bf$Ultimate),
                       sum(benk$Ultimate), sum(cc$Ultimate), sum(detail$Blended)),
    Total.IBNR = c(sum(detail$CL) - sum(latest),
                   sum(detail$Expected) - sum(latest),
                   sum(bf$IBNR), sum(benk$IBNR), sum(cc$IBNR),
                   sum(detail$Blended) - sum(latest))
  )

  list(detail = detail, methods_long = methods_long, totals = totals,
       prior = prior_tbl, kappa = kappa, prior_name = prior,
       elr_premium = elr_premium, elr_capecod = elr_capecod,
       has_exposure = has_exposure)
}

# ============================================================================
#  RECAST (BACK-TEST) ANALYSIS
# ----------------------------------------------------------------------------
#  Hold back the most recent `n_holdback` calendar diagonals, re-fit Mack on
#  the truncated triangle, project forward, and compare the PROJECTED values
#  against the ACTUAL values we held back. This is the actuarial
#  "actual vs expected" validation of the development assumptions.
# ============================================================================

# Remove the latest n diagonals from a cumulative triangle (set to NA).
truncate_diagonals <- function(cum_tri, n_holdback) {
  n_org <- nrow(cum_tri); n_dev <- ncol(cum_tri)
  out <- cum_tri
  for (i in seq_len(n_org)) {
    last_obs <- n_org - i + 1            # leading-diagonal position for row i
    keep_to  <- last_obs - n_holdback
    if (keep_to < 1) keep_to <- 0
    if (keep_to < n_dev) {
      cols <- (keep_to + 1):n_dev
      cols <- cols[cols <= n_dev & cols >= 1]
      out[i, cols] <- NA_real_
    }
  }
  out
}

recast_analysis <- function(cum_tri, n_holdback = 1) {
  n_org <- nrow(cum_tri); n_dev <- ncol(cum_tri)
  if (n_org - n_holdback < 2)
    stop("Holdback of ", n_holdback, " leaves fewer than 2 origin periods. ",
         "This triangle has ", n_org, " origins; try a smaller holdback.")

  truncated <- truncate_diagonals(cum_tri, n_holdback)

  # Holding back N diagonals empties the N rightmost development columns (no
  # early data remains to project into them). Drop fully-empty trailing columns
  # before fitting — you can't back-test development into a column you have no
  # observations for. We still keep the held-back cells in the EARLIER columns
  # for the Actual-vs-Expected comparison.
  col_counts <- colSums(!is.na(truncated))
  keep_cols  <- which(col_counts > 0)
  if (length(keep_cols) < 2)
    stop("Holdback of ", n_holdback, " removes almost all development history ",
         "(only ", length(keep_cols), " development period(s) remain). ",
         "Use a smaller holdback, or switch to monthly granularity for a ",
         "larger triangle.")

  fit_tri <- truncated[, keep_cols, drop = FALSE]

  fit <- tryCatch(
    MackChainLadder(fit_tri, est.sigma = "Mack"),
    error = function(e)
      stop("Mack could not fit the truncated triangle at holdback ",
           n_holdback, ": ", conditionMessage(e),
           ". Try a smaller holdback."))
  # Project back into the full column space (held-back cells live here)
  projected <- matrix(NA_real_, n_org, n_dev, dimnames = dimnames(cum_tri))
  projected[, keep_cols] <- fit$FullTriangle

  # Compare cells that were ACTUAL in the full triangle but HELD BACK (NA) in
  # the truncated one.
  cmp <- list()
  for (i in seq_len(n_org)) {
    for (j in seq_len(ncol(cum_tri))) {
      actual <- cum_tri[i, j]
      held   <- is.na(truncated[i, j]) && !is.na(actual)
      if (held) {
        proj <- projected[i, j]
        if (is.na(proj)) next        # cell fell in a dropped (empty) column
        cmp[[length(cmp) + 1]] <- tibble(
          Origin   = rownames(cum_tri)[i],
          DevLag   = j,
          Actual   = actual,
          Expected = proj,
          AvE_Diff = actual - proj,
          AvE_Pct  = ifelse(proj == 0, NA_real_, (actual - proj) / proj)
        )
      }
    }
  }
  if (!length(cmp)) stop("No held-back cells available for comparison.")
  detail <- bind_rows(cmp)

  summary_tbl <- detail %>%
    summarise(
      Cells        = n(),
      Total.Actual = sum(Actual),
      Total.Expected = sum(Expected),
      Total.Diff   = sum(AvE_Diff),
      Total.Pct    = sum(AvE_Diff) / sum(Expected),
      MAPE         = mean(abs(AvE_Pct), na.rm = TRUE),
      RMSE         = sqrt(mean(AvE_Diff^2))
    )

  list(detail = detail, summary = summary_tbl,
       truncated = truncated, projected = projected)
}

# ============================================================================
#  SEASONALITY ANALYSIS
# ----------------------------------------------------------------------------
#  Two complementary views of monthly pattern:
#   (a) Origin-month seasonality  -> are claims occurring (originating)
#       more in some months?
#   (b) Payment-month seasonality -> are claims paid more in some months?
#  We index each month against the overall average (=100) so a value of 120
#  means "20% above a flat seasonal pattern".
# ============================================================================

seasonality_analysis <- function(df) {
  origin_seas <- df %>%
    mutate(Mnth = month(OriginDate, label = TRUE, abbr = TRUE),
           Yr   = year(OriginDate)) %>%
    group_by(Yr, Mnth) %>%
    summarise(Amount = sum(ClaimAmount, na.rm = TRUE), .groups = "drop") %>%
    group_by(Mnth) %>%
    summarise(MeanAmount = mean(Amount), .groups = "drop") %>%
    mutate(Index = 100 * MeanAmount / mean(MeanAmount),
           Basis = "Origin month")

  pay_seas <- df %>%
    mutate(Mnth = month(PaymentDate, label = TRUE, abbr = TRUE),
           Yr   = year(PaymentDate)) %>%
    group_by(Yr, Mnth) %>%
    summarise(Amount = sum(ClaimAmount, na.rm = TRUE), .groups = "drop") %>%
    group_by(Mnth) %>%
    summarise(MeanAmount = mean(Amount), .groups = "drop") %>%
    mutate(Index = 100 * MeanAmount / mean(MeanAmount),
           Basis = "Payment month")

  combined <- bind_rows(origin_seas, pay_seas) %>%
    mutate(Mnth = factor(Mnth, levels = month.abb))

  # Heatmap source: payment-month x payment-year totals
  heat <- df %>%
    mutate(Mnth = month(PaymentDate, label = TRUE, abbr = TRUE),
           Yr   = factor(year(PaymentDate))) %>%
    group_by(Yr, Mnth) %>%
    summarise(Amount = sum(ClaimAmount, na.rm = TRUE), .groups = "drop") %>%
    mutate(Mnth = factor(Mnth, levels = month.abb))

  list(index = combined, heat = heat)
}

# ============================================================================
#  EXPOSURE / EARNED PREMIUM TABLE
# ----------------------------------------------------------------------------
#  The user supplies a small table with three columns:
#     Period          - the ORIGIN period the exposure relates to. Accepts a
#                       date (2021-01-01), a year-month (2021-01 / Jan-2021),
#                       or a quarter (2021 Q1 / 2021-Q1). Parsed to a Date at
#                       the period start so it can be matched to triangle rows.
#     EarnedPremium   - earned premium for that origin period (drives the
#                       BF / Benktander a-priori).
#     Exposure        - exposure measure for that origin period (drives the
#                       Cape Cod ELR). May equal earned premium if the user has
#                       no separate exposure measure.
# ============================================================================

# Parse a flexible Period cell into a Date at the period start.
parse_period_cell <- function(x) {
  if (inherits(x, "Date"))    return(as.Date(x))
  if (inherits(x, "POSIXct")) return(as.Date(x))
  s <- trimws(as.character(x))
  if (is.na(s) || s == "") return(as.Date(NA))

  # Excel serial number (numeric date) -> Date
  if (grepl("^[0-9]+(\\.[0-9]+)?$", s)) {
    n <- suppressWarnings(as.numeric(s))
    if (!is.na(n) && n > 30000 && n < 80000)             # plausible serial
      return(as.Date(n, origin = "1899-12-30"))
  }

  # Quarter forms: 2021 Q1, 2021-Q1, Q1 2021, 2021Q1
  m <- regmatches(s, regexec("(?i)^\\s*(\\d{4})\\s*[- ]?q\\s*([1-4])\\s*$", s))[[1]]
  if (length(m) == 3) {
    yr <- as.integer(m[2]); q <- as.integer(m[3])
    return(as.Date(sprintf("%04d-%02d-01", yr, (q - 1) * 3 + 1)))
  }
  m <- regmatches(s, regexec("(?i)^\\s*q\\s*([1-4])\\s*[- ]?(\\d{4})\\s*$", s))[[1]]
  if (length(m) == 3) {
    q <- as.integer(m[2]); yr <- as.integer(m[3])
    return(as.Date(sprintf("%04d-%02d-01", yr, (q - 1) * 3 + 1)))
  }

  # Month-name forms: Jan-2021, Jan 2021, 2021 Jan
  for (fmt in c("%b-%Y", "%b %Y", "%B-%Y", "%B %Y", "%Y-%b", "%Y %b")) {
    d <- suppressWarnings(as.Date(paste0("01-", s), format = paste0("%d-", fmt)))
    if (!is.na(d)) return(d)
    d <- suppressWarnings(lubridate::parse_date_time(s, orders = fmt))
    if (!is.na(d)) return(as.Date(floor_date(d, "month")))
  }

  # Year-month / full date: 2021-01, 2021/01, 2021-01-15, 2021/1/1
  d <- suppressWarnings(lubridate::ymd(s, quiet = TRUE))
  if (!is.na(d)) return(as.Date(d))
  d <- suppressWarnings(lubridate::ym(s, quiet = TRUE))
  if (!is.na(d)) return(as.Date(d))
  d <- suppressWarnings(as.Date(s))
  if (!is.na(d)) return(d)

  as.Date(NA)
}

# Read + validate an uploaded exposure workbook into a tidy frame.
read_exposure_table <- function(path) {
  ex <- readxl::read_excel(path, sheet = 1)
  nm <- names(ex)

  # Tolerant column matching (case / spacing / common synonyms).
  find_col <- function(cands) {
    hit <- which(tolower(gsub("[^a-z]", "", tolower(nm))) %in%
                 gsub("[^a-z]", "", tolower(cands)))
    if (length(hit)) nm[hit[1]] else NA_character_
  }
  c_period <- find_col(c("Period", "OriginPeriod", "Origin", "OriginDate",
                         "AccidentPeriod", "Month", "Quarter"))
  c_prem   <- find_col(c("EarnedPremium", "EarnedPrem", "Premium",
                         "EP", "EarnedPrem"))
  c_exp    <- find_col(c("Exposure", "Exposures", "Exp", "Units",
                         "EarnedExposure"))

  if (is.na(c_period))
    stop("Exposure file needs a 'Period' column (origin period).")
  if (is.na(c_prem) && is.na(c_exp))
    stop("Exposure file needs at least an 'EarnedPremium' or 'Exposure' column.")

  prem <- if (!is.na(c_prem)) as.numeric(ex[[c_prem]]) else NA_real_
  expo <- if (!is.na(c_exp))  as.numeric(ex[[c_exp]])  else NA_real_
  # If only one of the two is given, mirror it so both methods can run.
  if (all(is.na(prem))) prem <- expo
  if (all(is.na(expo))) expo <- prem

  # Parse each Period cell to a Date (class-safe across Date/character/numeric
  # columns). Build the list element-by-element to preserve per-cell class.
  pcol     <- ex[[c_period]]
  pdates   <- as.Date(vapply(seq_along(pcol), function(k)
                as.character(parse_period_cell(pcol[[k]])), character(1)))

  out <- tibble(
    Period        = as.character(pdates),
    PeriodDate    = pdates,
    EarnedPremium = prem,
    Exposure      = expo
  ) %>% filter(!is.na(PeriodDate))

  if (!nrow(out))
    stop("No rows in the exposure file had a recognisable Period value.")
  out
}

# Align an exposure frame to the rows of a cumulative triangle. Triangle
# rownames are origin-period start Dates (monthly or quarterly). We floor both
# sides to the triangle granularity and match, so a monthly exposure table can
# be aggregated up to a quarterly triangle and vice-versa.
align_exposure <- function(exposure_df, cum_tri, period = c("month", "quarter")) {
  period <- match.arg(period)
  origins <- as.Date(rownames(cum_tri))

  ex <- exposure_df %>%
    mutate(Key = floor_date(PeriodDate, period)) %>%
    group_by(Key) %>%
    summarise(EarnedPremium = sum(EarnedPremium, na.rm = TRUE),
              Exposure      = sum(Exposure, na.rm = TRUE),
              .groups = "drop")

  key_org <- floor_date(origins, period)
  idx     <- match(key_org, ex$Key)

  ep  <- ex$EarnedPremium[idx]
  exo <- ex$Exposure[idx]
  # Zeros are treated as "not supplied" downstream (the engines guard >0).
  list(
    earned_premium = ep,
    exposure       = exo,
    matched        = sum(!is.na(idx)),
    n_origins      = length(origins),
    aligned        = tibble(Origin = as.character(origins),
                            EarnedPremium = ep, Exposure = exo)
  )
}

ui <- page_sidebar(
  title = "Medical LOB Reserving Tool",
  theme = bs_theme(version = 5, bootswatch = "flatly",
                   primary = "#2C3E50", base_font = font_google("Inter")),

  sidebar = sidebar(
    width = 320,
    title = "Controls",

    fileInput("data_file", "Claims data (.xlsx)", accept = ".xlsx"),

    fileInput("exposure_file",
              "Exposure / earned premium (.xlsx, optional)",
              accept = ".xlsx"),
    div(style = "font-size:.78em; color:#7F8C8D; margin-top:-8px; margin-bottom:6px;",
        "Columns: Period, EarnedPremium, Exposure. Drives the exposure-based",
        " Cape Cod, Bornhuetter-Ferguson and Benktander a-priori."),

    uiOutput("subcat_selector_ui"),

    radioButtons("period", "Triangle granularity",
                 choices = c("Monthly" = "month", "Quarterly" = "quarter"),
                 selected = "quarter", inline = TRUE),

    numericInput("tail", "Tail factor (Mack)", value = 1.00,
                 min = 1, max = 3, step = 0.01),

    hr(),
    h6("Bootstrap"),
    numericInput("n_sims", "Simulations", value = 1000,
                 min = 100, max = 10000, step = 100),
    selectInput("boot_dist", "Process distribution",
                choices = c("Gamma" = "gamma",
                            "Over-dispersed Poisson" = "od.pois")),

    hr(),
    h6("Recast / back-test"),
    numericInput("n_holdback", "Diagonals to hold back", value = 1,
                 min = 1, max = 6, step = 1),

    hr(),
    h6("Blended estimate"),
    selectInput("blend_prior", "Stabilising prior",
                choices = c("Benktander (iterated BF)" = "benktander",
                            "Bornhuetter-Ferguson"     = "bf",
                            "Cape Cod"                 = "capecod"),
                selected = "benktander"),
    sliderInput("blend_kappa", "Credibility steepness (kappa)",
                min = 0.5, max = 3, value = 1.5, step = 0.1),
    checkboxInput("blend_seasonal", "Seasonally adjust the prior", value = TRUE),

    hr(),
    actionButton("run_analysis", "Run analysis",
                 icon = icon("play"), class = "btn-primary w-100"),
    br(), br(),
    uiOutput("download_button_ui")
  ),

  navset_card_tab(
    id = "main_tabs",

    nav_panel("1. Data & Checks",
      card(card_header("Uploaded data (post-cleaning)"),
           DTOutput("raw_data_table"),
           explain_block(
             exec = paste("This is the raw claims data you uploaded, after we removed",
                          "blank or invalid rows. The boxes below summarise how many",
                          "records came through, the total dollars paid, and the date",
                          "range covered — a quick sanity check that the right file loaded."),
             technical = paste("Transactional/aggregated claim records filtered to drop",
                               "rows with missing OriginDate, PaymentDate, or ClaimAmount,",
                               "and any record where PaymentDate < OriginDate. Dates are",
                               "coerced to Date, ClaimAmount to numeric, SubCat to factor.",
                               "No de-duplication is applied here; identical Origin/Payment/SubCat",
                               "rows are summed downstream during triangle construction."))
      ),
      layout_columns(
        value_box("Rows", textOutput("vb_rows"),  showcase = icon("table")),
        value_box("Total paid", textOutput("vb_total"), showcase = icon("coins")),
        value_box("Origin span", textOutput("vb_span"), showcase = icon("calendar"))
      )
    ),

    nav_panel("1b. Exposure & Premium",
      uiOutput("exposure_status"),
      card(card_header("Exposure / earned premium by period"),
           DTOutput("exposure_table"),
           explain_block(
             exec = paste("This is the earned premium and exposure you provided for",
                          "each period, lined up against the periods in the claims",
                          "triangle. Earned premium and exposure tell the model how much",
                          "business was actually on risk in each period, so the reserve",
                          "for the newest, least-developed periods can be anchored to how",
                          "much was written rather than guessed from a few early payments.",
                          "If a period shows blank, no exposure was matched to it and the",
                          "model falls back to its data-only estimate for that period."),
             technical = paste("Exposure base aligned to origin rows of the cumulative",
                               "triangle. Period values are parsed (date, year-month, or",
                               "year-quarter) and floored to the triangle granularity, so a",
                               "monthly exposure table aggregates up to a quarterly triangle.",
                               "Earned premium feeds the Bornhuetter-Ferguson / Benktander",
                               "a-priori; exposure feeds the Cape Cod (Stanard-Buhlmann) ELR.",
                               "Both a-priori loss ratios are estimated on a developed basis:",
                               "ELR = sum(latest) / sum(base * %developed)."))
      ),
      layout_columns(
        value_box("Periods matched", textOutput("vb_exp_matched"),
                  showcase = icon("link")),
        value_box("Total earned premium", textOutput("vb_exp_premium"),
                  showcase = icon("file-invoice-dollar")),
        value_box("Total exposure", textOutput("vb_exp_exposure"),
                  showcase = icon("layer-group"))
      ),
      card(card_header("Fitted a-priori loss ratios (developed basis)"),
           DTOutput("exposure_elr_table"),
           explain_block(
             exec = paste("The implied loss ratio the model has read from your data and",
                          "premium - roughly, expected claims as a share of premium. It is",
                          "what the exposure-based methods use to project the immature",
                          "periods. A figure that looks out of line with pricing",
                          "expectations is worth questioning before booking the reserve."),
             technical = paste("Developed-basis (Cape Cod / Stanard-Buhlmann) a-priori loss",
                               "ratios: the premium ELR drives BF and Benktander, the exposure",
                               "ELR drives Cape Cod. Each is sum(latest) over sum(base x",
                               "%developed), i.e. claims emerged to date divided by the earned,",
                               "developed portion of the exposure base. Shown only when an",
                               "exposure file is loaded and the analysis has been run."))
      )
    ),

    nav_panel("2. Cumulative Triangle",
      card(card_header(textOutput("cum_caption")),
           DTOutput("cumulative_triangle_table"),
           explain_block(
             exec = paste("A 'triangle' tracks how claim payments for each starting period",
                          "(rows) pile up as time passes (columns). Reading left to right",
                          "shows how much had been paid so far. The blank upper-right corner",
                          "is simply the future — payments that haven't happened yet. This",
                          "shape is the raw material for estimating money still owed."),
             technical = paste("Cumulative run-off triangle. Rows are origin periods (monthly",
                               "or quarterly), columns are development lags. Cell (i, j) is the",
                               "running total of incremental claims for origin i through development",
                               "period j. Only cells on or below the leading diagonal are populated;",
                               "upper-right cells are NA (unobserved future development)."))
      )),

    nav_panel("3. Incremental Triangle",
      card(card_header(textOutput("incr_caption")),
           DTOutput("incremental_triangle_table"),
           explain_block(
             exec = paste("The same triangle, but showing the new dollars paid in each",
                          "period rather than the running total. This makes it easy to see",
                          "the payment 'rhythm' — typically a lot early on, tapering off as",
                          "older claims close out."),
             technical = paste("Incremental claims matrix: cell (i, j) is the standalone",
                               "(non-cumulative) paid amount for origin i at development lag j,",
                               "obtained by summing ClaimAmount per origin/dev cell. Cumulating",
                               "across columns reproduces the cumulative triangle. NA cells",
                               "denote unobserved future development."))
      )),

    nav_panel("4. Development Factors",
      card(card_header("Selected age-to-age (link) factors"),
           DTOutput("dev_factor_table"),
           explain_block(
             exec = paste("These multipliers describe how claims typically grow from one",
                          "period to the next. A factor of 1.30 means claims tend to grow",
                          "30% from that age to the next. Factors close to 1.00 mean claims",
                          "have largely stopped developing. They are the engine that projects",
                          "today's partial claims out to their eventual final cost."),
             technical = paste("Volume-weighted age-to-age (link) ratios f_k selected by the Mack",
                               "Chain Ladder, where f_k is the ratio of column k+1 to column k",
                               "cumulative totals over origins with both observed. Multiplying",
                               "the latest diagonal by the running product of remaining factors",
                               "(plus any tail factor) yields projected ultimates."))
      )),

    nav_panel("5. Reserve Estimates",
      card(card_header("Mack Chain Ladder — by origin"),
           DTOutput("reserve_estimates_table"),
           explain_block(
             exec = paste("The headline result: for each starting period, how much has been",
                          "paid so far (Latest), the estimated final cost (Ultimate), and the",
                          "gap still expected to be paid (IBNR — the reserve we need to hold).",
                          "The last column flags how uncertain each estimate is — larger means",
                          "less mature, less reliable. The 'Total' row is the bottom line."),
             technical = paste("Mack Chain Ladder by-origin summary. Latest = most recent",
                               "cumulative; Dev.To.Date = Latest/Ultimate; Ultimate = projected",
                               "final; IBNR = Ultimate − Latest; Mack.S.E = Mack's analytic",
                               "standard error of the IBNR (captures estimation + process error);",
                               "CV = coefficient of variation of IBNR (S.E. / IBNR). Totals row",
                               "uses Mack's correlated aggregation, not a simple column sum of S.E."))
      ),
      card(card_header("Method comparison — total IBNR"),
           DTOutput("method_compare_table"),
           explain_block(
             exec = paste("A side-by-side of the reserve estimate produced by each method.",
                          "When the different methods land in roughly the same place, that's",
                          "reassuring. A wide spread is a signal to dig deeper before settling",
                          "on a number."),
             technical = paste("Total IBNR and its standard error across Mack Chain Ladder,",
                               "Bootstrap ODP (mean and SD of simulated reserves),",
                               "Bornhuetter-Ferguson, and Cape Cod. When an exposure /",
                               "earned-premium file is loaded, BF uses a premium a-priori and",
                               "Cape Cod uses an exposure-weighted ELR (both on a developed",
                               "basis); otherwise they fall back to CL-seeded and",
                               "equal-exposure assumptions respectively. Material divergence",
                               "usually points to thin tails, an unstable diagonal, or",
                               "sensitivity to the a-priori assumption."))
      )),

    nav_panel("6. Bootstrap Distribution",
      fillable = FALSE,
      layout_columns(
        card(fill = FALSE, min_height = "520px",
             card_header("Predictive distribution of total IBNR"),
             plotOutput("boot_hist", height = "360px"),
             explain_block(
               exec = paste("Instead of a single reserve number, this shows the full range of",
                            "plausible outcomes from thousands of simulated scenarios. Taller",
                            "bars are more likely results. The dashed lines mark the midpoint and",
                            "the high-end (75th and 95th) cases — useful for thinking about how",
                            "much cushion to hold for a bad year."),
               technical = paste("Histogram of total IBNR across R bootstrap replications of the",
                                 "over-dispersed Poisson chain ladder (Gamma or ODP process",
                                 "distribution). Dashed verticals mark the 50th, 75th, and 95th",
                                 "percentiles of the simulated total-reserve distribution,",
                                 "capturing combined estimation and process variability."))
        ),
        card(fill = FALSE, card_header("Quantiles"), DTOutput("boot_quantile_table"),
             explain_block(
               exec = paste("The same simulation results as a table. It reads as: 'there's a 75%",
                            "chance the reserve need comes in at or below this figure.' Higher",
                            "percentiles (95%, 99%) are the stress / worst-case levels."),
               technical = paste("Summary statistics (mean, SD) and selected percentiles",
                                 "(5/25/50/75/95/99/99.5%) of the simulated total-IBNR vector.",
                                 "Upper quantiles support risk-margin / capital and reinsurance",
                                 "attachment discussions."))
        ),
        col_widths = c(7, 5)
      ),
      card(fill = FALSE, card_header("Bootstrap by origin"), DTOutput("boot_origin_table"),
           explain_block(
             exec = paste("Breaks the simulated reserve down by starting period, showing the",
                          "average estimate and its variability for each. This pinpoints which",
                          "periods contribute most of the uncertainty."),
             technical = paste("Per-origin bootstrap summary: simulated IBNR mean, standard error,",
                               "and percentiles by origin period. Origin-level S.E.s do not add to",
                               "the total S.E. because of diversification across origins."))
      )
    ),

    nav_panel("7. Mack Diagnostics",
      fillable = FALSE,
      layout_columns(
        card(fill = FALSE, min_height = "540px",
             card_header("Mack summary plots"), plotOutput("mack_plot1", height = "380px"),
             explain_block(
               exec = paste("A visual health check on the projection. It shows the claims paid",
                            "to date alongside the projected remainder for each period, with the",
                            "uncertainty drawn as error bars. Bigger bars mean a less certain",
                            "estimate — typically the most recent, least-developed periods."),
               technical = paste("Standard MackChainLadder summary panel: latest cumulative plus",
                                 "projected IBNR by origin with Mack standard-error bars, and the",
                                 "aggregated forecast. Conveys the relative scale of estimation",
                                 "uncertainty across origin periods."))
        ),
        card(fill = FALSE, min_height = "540px",
             card_header("Residual diagnostics"), plotOutput("mack_plot2", height = "380px"),
             explain_block(
               exec = paste("This checks whether the model's assumptions hold up. Ideally the",
                            "dots scatter randomly around the centre line with no pattern. A",
                            "clear trend or fanning shape is a warning that the standard method",
                            "may be over- or under-stating the reserve and needs expert review."),
               technical = paste("Standardised residuals plotted against development period, origin",
                                 "period, and fitted values. Patterns, trends, or heteroscedasticity",
                                 "violate Mack's assumptions (independent, proportional-variance",
                                 "development), flagging the need for tail adjustment, weighting,",
                                 "or an alternative model."))
        )
      )
    ),

    nav_panel("8. Blended Estimate",
      fillable = FALSE,
      div(class = "alert alert-info",
          tags$b("Why this tab. "),
          paste("Chain Ladder is reliable for mature origin periods but becomes",
                "erratic for the newest ones - it multiplies a tiny, noisy latest",
                "figure by the full remaining development pattern, so the youngest",
                "period (and lag-1 cells, especially on monthly data) can swing wildly.",
                "Here we anchor the green periods to a stable, seasonally-adjusted",
                "prior and let each period's own data earn weight only as it matures.")),
      card(fill = FALSE,
           card_header("Recommended ultimate & IBNR by origin (blended)"),
           DTOutput("blend_detail_table"),
           explain_block(
             exec = paste("This is the recommended reserve. For older periods it follows the",
                          "standard Chain Ladder estimate (which works well once a period is",
                          "mostly settled). For the newest periods it leans on a stable",
                          "expected level instead, because their raw projections are too",
                          "jumpy to trust. The 'Credibility' column shows the mix: near 1.00",
                          "means almost all Chain Ladder; near 0 means almost all the stable",
                          "prior."),
             technical = paste("Per-origin blend Ultimate = Z * CL + (1 - Z) * Prior, with",
                               "maturity credibility Z = (percent-developed)^kappa and Prior drawn",
                               "from the selected BF / Benktander / Cape Cod family. Columns show",
                               "each candidate estimator (CL, Expected/seasonal, BF, Benktander,",
                               "Cape Cod) plus the blended result and the implied CL vs blended IBNR."))
      ),
      card(fill = FALSE,
           card_header("Method comparison — totals"),
           DTOutput("blend_totals_table"),
           explain_block(
             exec = paste("The total reserve each method would produce. The blended figure is",
                          "the recommendation; the others are shown so you can see how much the",
                          "choice of method moves the bottom line. A large spread is a signal",
                          "the newest periods are driving most of the uncertainty."),
             technical = paste("Aggregate ultimate and IBNR across Chain Ladder, Expected/seasonal",
                               "prior, Bornhuetter-Ferguson, Benktander, Cape Cod, and the blend.",
                               "Divergence concentrates in low-maturity origins where CL leverage",
                               "is highest."))
      ),
      card(fill = FALSE, min_height = "520px",
           card_header("Ultimate by origin — method curves"),
           plotOutput("blend_curve_plot", height = "380px"),
           explain_block(
             exec = paste("Each line is one method's estimate across the starting periods (oldest",
                          "on the left, newest on the right). On the left the lines sit on top of",
                          "each other - everyone agrees once a period is mature. On the right they",
                          "fan apart, and that fan is exactly the instability we are managing. The",
                          "blended line stays in the sensible middle of the fan."),
             technical = paste("Ultimate by origin for every estimator. Convergence at high maturity",
                               "and divergence at low maturity visualises CL's tail leverage. The",
                               "blended series tracks CL where Z is high and migrates toward the",
                               "prior as Z falls."))
      ),
      card(fill = FALSE, min_height = "460px",
           card_header("Credibility weighting by maturity"),
           plotOutput("blend_credibility_plot", height = "320px"),
           explain_block(
             exec = paste("Shows how much trust each period's own data is given, based on how",
                          "developed it is. Fully-developed periods get full trust; brand-new",
                          "periods get little, with the gap filled by the stable prior. The",
                          "steepness slider in the sidebar controls how cautious this is."),
             technical = paste("Credibility Z = (percent-developed)^kappa plotted against origin",
                               "maturity. Raising kappa pushes Z down for immature periods (more",
                               "prior weight, more stability); lowering it lets data earn weight",
                               "sooner (more responsiveness)."))
      ),
      card(fill = FALSE, min_height = "500px",
           card_header("CL vs Blended IBNR by origin"),
           plotOutput("blend_ibnr_plot", height = "360px"),
           explain_block(
             exec = paste("A direct before/after on the reserve held per period: raw Chain Ladder",
                          "next to the blended recommendation. The bars match for older periods and",
                          "differ most for the newest ones - which is precisely where the blend is",
                          "doing its job of taming volatile estimates."),
             technical = paste("Grouped bars of IBNR (Ultimate minus Latest) under CL vs the blend",
                               "by origin. Differences are concentrated in the youngest origins;",
                               "mature origins are unchanged because Z approx 1 there."))
      )
    ),

    nav_panel("9. Actuary Adjustments",
      fillable = FALSE,
      div(class = "alert alert-info",
          tags$b("Why this tab. "),
          paste("No reserving model captures everything an actuary knows -",
                "pending large claims, benefit changes, processing backlogs,",
                "management actions. This worksheet lets you overlay that",
                "judgement on the model output, per origin period, with an",
                "auditable rationale. Double-click a white cell to edit it:",
                "choose the selection Basis (CL, BF, Benktander, CapeCod,",
                "Blended or Expected), apply a percentage load (Adj %), a",
                "dollar add-on (Adj Amount), or type a hard Override ultimate,",
                "and record your reasoning in Comment. Adjustments flow",
                "straight through to the Final Summary tab and the Excel",
                "export.")),
      card(fill = FALSE,
           card_header("Bulk actions"),
           layout_columns(
             selectInput("adj_basis_all", "Selection basis for all origins",
                         choices = c("Blended", "CL", "BF", "Benktander",
                                     "CapeCod", "Expected"),
                         selected = "Blended"),
             div(style = "padding-top:32px;",
                 actionButton("adj_apply_basis", "Apply basis to all",
                              icon = icon("layer-group"),
                              class = "btn-outline-primary w-100")),
             div(style = "padding-top:32px;",
                 actionButton("adj_reset", "Reset all adjustments",
                              icon = icon("rotate-left"),
                              class = "btn-outline-danger w-100")),
             col_widths = c(6, 3, 3))
      ),
      card(fill = FALSE,
           card_header("Selection & judgemental overlay worksheet (double-click a cell to edit)"),
           DTOutput("adj_table"),
           explain_block(
             exec = paste("Each row is one origin period. 'Base Ultimate' is what the",
                          "chosen method says; 'Selected Ultimate' is that figure after",
                          "your adjustments. Edit Basis, Adj %, Adj Amount, Override or",
                          "Comment by double-clicking. A hard Override wins over the",
                          "percentage and dollar adjustments. Everything you type is",
                          "kept in the audit log with a timestamp."),
             technical = paste("Selected Ultimate = Override if supplied, otherwise",
                               "Base(Basis) * (1 + AdjPct/100) + AdjAmt. Basis draws from",
                               "the estimator set computed on the Blended tab (CL, Expected,",
                               "BF, Benktander, Cape Cod, Blended). Selected IBNR = Selected",
                               "Ultimate - Latest. Edits re-run reactively; re-running the",
                               "analysis (new data / settings) re-initialises the worksheet",
                               "since prior overlays no longer refer to the same model."))
      ),
      layout_columns(
        value_box("Model IBNR (pre-adjustment)", textOutput("vb_adj_base"),
                  showcase = icon("scale-balanced")),
        value_box("Booked IBNR (after adjustment)", textOutput("vb_adj_final"),
                  showcase = icon("user-pen")),
        value_box("Judgemental overlay", textOutput("vb_adj_delta"),
                  showcase = icon("arrows-up-down"))
      ),
      uiOutput("adj_guardrail"),
      card(fill = FALSE, min_height = "500px",
           card_header("Model vs booked IBNR by origin"),
           plotOutput("adj_compare_plot", height = "360px"),
           explain_block(
             exec = paste("A live before/after of the reserve per period: the model's",
                          "figure next to your adjusted figure. Bars that differ are the",
                          "periods you have overridden - a quick visual check that the",
                          "adjustments landed where you intended and nothing was fat-fingered."),
             technical = paste("Grouped bars of IBNR under the selected basis before and",
                               "after the judgemental overlay, by origin. Updates reactively",
                               "with every cell edit; origins with a hard Override are marked.")))
    ),

    nav_panel("10. Final Summary",
      fillable = FALSE,
      div(class = "alert alert-secondary",
          tags$b("How to read this page. "),
          paste("This is the post-adjustment brief: the reserve you are actually",
                "booking after actuarial judgement, how it differs from the pure",
                "model answer, where it sits in the simulated range of outcomes,",
                "and the audit trail of every change. It updates live as you edit",
                "the Adjustments tab. Complete the sign-off block before exporting",
                "- it is written into the Excel download.")),
      uiOutput("final_headline_ui"),
      card(fill = FALSE, min_height = "520px",
           card_header("Reserve walk - model estimate to booked figure"),
           plotOutput("final_waterfall", height = "380px"),
           explain_block(
             exec = paste("A bridge from the model's reserve to the booked reserve. Each",
                          "middle bar is one period's judgemental adjustment - up-bars add",
                          "reserve, down-bars release it - so anyone reviewing can see",
                          "exactly which decisions moved the bottom line and by how much."),
             technical = paste("Waterfall of total IBNR: model total under the selected bases,",
                               "one increment per origin with a non-zero judgement (largest",
                               "eight shown; the remainder grouped as 'Other'), closing at the",
                               "booked total. Latest paid is unchanged by adjustments, so",
                               "ultimate deltas equal IBNR deltas."))
      ),
      card(fill = FALSE, min_height = "500px",
           card_header("IBNR by origin - Chain Ladder vs model selection vs booked"),
           plotOutput("final_ibnr_plot", height = "360px"),
           explain_block(
             exec = paste("Three views of the reserve for each period: the raw Chain Ladder",
                          "answer, the model's blended/selected answer, and the final booked",
                          "figure after judgement. It shows at a glance how far the booked",
                          "number has moved from the mechanical estimates, and where."),
             technical = paste("Grouped bars of per-origin IBNR: pure CL (Ultimate_CL - Latest),",
                               "the pre-adjustment selected basis, and the post-adjustment booked",
                               "figure. Differences between the last two are the judgemental",
                               "overlay; differences between the first two are the model blend."))
      ),
      card(fill = FALSE, min_height = "500px",
           card_header("Booked reserve against the simulated distribution"),
           plotOutput("final_dist_plot", height = "360px"),
           explain_block(
             exec = paste("Where the booked reserve lands in the range of simulated outcomes.",
                          "A booked figure near the middle is a best-estimate stance; one out",
                          "in the right tail is deliberately prudent; one in the left tail is",
                          "optimistic and worth a second look before sign-off."),
             technical = paste("Bootstrap ODP predictive distribution of total IBNR with verticals",
                               "at the pre-adjustment model total (dashed) and the booked total",
                               "(solid). The subtitle reports the booked figure's empirical",
                               "percentile within the simulated distribution - an implicit",
                               "confidence-level reading of the judgemental margin."))
      ),
      card(fill = FALSE,
           card_header("Final selection by origin"),
           DTOutput("final_table"),
           explain_block(
             exec = paste("The definitive per-period table: what has been paid, what the model",
                          "said, what you selected, and the resulting reserve to book - with",
                          "the size of the judgement called out in its own column."),
             technical = paste("Per-origin final selection: Latest, %developed, selection basis,",
                               "base ultimate, adjustment terms, selected ultimate, selected IBNR,",
                               "and Judgement = Selected - Base ultimate. This table is exported",
                               "verbatim to the 'Final Selection' sheet of the Excel download."))
      ),
      card(fill = FALSE,
           card_header("Adjustment audit log"),
           DTOutput("adj_log_table"),
           explain_block(
             exec = paste("Every judgement call in one place: which periods were touched, what",
                          "was changed, by how much, why, and when. This is the trail a peer",
                          "reviewer, auditor, or regulator will ask for."),
             technical = paste("Rows of the adjustment worksheet with a non-zero judgement or an",
                               "edit timestamp: basis, percentage load, dollar add-on, override,",
                               "resulting judgement, comment, and last-edited time. Exported to",
                               "the 'Adjustment Log' sheet."))
      ),
      card(fill = FALSE,
           card_header("Sign-off"),
           layout_columns(
             textInput("signoff_name", "Reviewing actuary", ""),
             textInput("signoff_role", "Role / credential", ""),
             col_widths = c(6, 6)),
           textAreaInput("signoff_notes",
                         "Overall rationale / basis of selection",
                         "", rows = 3, width = "100%"),
           explain_block(
             exec = paste("Record who reviewed the reserve and the overall reasoning. These",
                          "fields are stamped into the Excel export together with the booked",
                          "figure and the date, closing the governance loop."),
             technical = paste("Free-text sign-off captured client-side and written to a",
                               "'Sign-off' sheet on download: reviewer, role, rationale, model",
                               "total IBNR, booked total IBNR, judgemental overlay, export date.")))
    ),

    nav_panel("11. Recast Analysis",
      fillable = FALSE,
      uiOutput("recast_status"),
      card(fill = FALSE,
           card_header("Back-test summary (Actual vs Expected on held-back diagonals)"),
           DTOutput("recast_summary_table"),
           explain_block(
             exec = paste("A track-record test: we hide the most recent actual data, ask the",
                          "model to predict it, then reveal the truth and score the prediction.",
                          "Small differences mean the method has been reliable on this data; a",
                          "large gap means treat the current estimates with extra caution."),
             technical = paste("Hold-out back-test. The latest N calendar diagonals are removed,",
                               "Mack is re-fit on the truncated triangle, projected forward, and",
                               "compared to the withheld actuals. Summary reports cell count, total",
                               "actual vs expected, total dollar and % difference, MAPE, and RMSE",
                               "of the actual-minus-expected errors."))
      ),
      card(fill = FALSE,
           card_header("Actual vs Expected — detail"),
           DTOutput("recast_detail_table"),
           explain_block(
             exec = paste("The cell-by-cell scorecard behind the summary above. It shows, for",
                          "each hidden value, what the model predicted versus what actually",
                          "happened, so you can see exactly where it ran high or low."),
             technical = paste("Per-cell Actual-vs-Expected detail for each held-back cell: origin,",
                               "development lag, actual cumulative, projected cumulative, dollar",
                               "difference, and percentage error. Cells in fully-emptied trailing",
                               "columns are excluded as non-projectable."))
      ),
      card(fill = FALSE, min_height = "500px",
           card_header("Actual vs Expected by origin"),
           plotOutput("recast_plot", height = "360px"),
           explain_block(
             exec = paste("The same back-test shown as bars per starting period: actual (dark)",
                          "next to predicted (grey). Where the two bars match, the model did",
                          "well; visible gaps highlight the periods where it was least accurate."),
             technical = paste("Grouped bar chart of held-back cumulative actuals vs Mack-projected",
                               "expecteds, aggregated by origin. Systematic one-sided gaps indicate",
                               "bias in the development assumptions for specific origin cohorts."))
      )
    ),

    nav_panel("12. Seasonality",
      fillable = FALSE,
      card(fill = FALSE, min_height = "500px",
           card_header("Seasonal index by month (100 = average)"),
           plotOutput("seasonality_plot", height = "360px"),
           explain_block(
             exec = paste("Shows whether certain months run busier than others. A value of 120",
                          "means that month is 20% above the yearly average; 80 means 20% below.",
                          "Two lines are drawn: when claims occur, and when they get paid —",
                          "useful for anticipating cash-flow peaks and staffing needs."),
             technical = paste("Monthly seasonal indices on two bases — origin month (incidence)",
                               "and payment month (settlement). Each month's mean amount is indexed",
                               "to 100 = overall monthly average. The dotted line at 100 marks a",
                               "flat (non-seasonal) pattern; deviations quantify seasonal lift/drag."))
      ),
      card(fill = FALSE, min_height = "460px",
           card_header("Payment heatmap (year x month)"),
           plotOutput("seasonality_heat", height = "320px"),
           explain_block(
             exec = paste("A colour grid of payments by year (rows) and month (columns). Darker",
                          "squares are heavier payment periods. It makes spikes, quiet stretches,",
                          "and year-over-year shifts easy to spot at a glance."),
             technical = paste("Tile heatmap of total paid claims by payment-year × payment-month.",
                               "Colour intensity scales with amount. Reveals calendar-period effects",
                               "(e.g. processing backlogs, fee-schedule changes) distinct from the",
                               "averaged seasonal index above."))
      ),
      card(fill = FALSE,
           card_header("Seasonal index table"),
           DTOutput("seasonality_table"),
           explain_block(
             exec = paste("The exact numbers behind the seasonality chart — the average amount",
                          "and the index for each month, on both the occurrence and payment",
                          "bases — for anyone who wants the precise figures."),
             technical = paste("Tabulated seasonal indices: basis, month, mean amount, and index",
                               "(100 = average) for both origin-month and payment-month bases,",
                               "sorted by basis then calendar month."))
      )
    ),

    nav_panel("13. Executive Summary",
      fillable = FALSE,
      div(class = "alert alert-secondary",
          tags$b("How to read this page. "),
          paste("This is a plain-language brief that pulls together every other tab",
                "into the handful of points that matter for a reserving decision.",
                "It updates automatically after you press Run analysis. Figures in",
                "the cards below are the recommended numbers; the notes underneath",
                "flag where to be careful.")),
      uiOutput("exec_summary_ui")
    )
  )
)

# ============================================================================
#  SERVER
# ============================================================================

server <- function(input, output, session) {

  # ---- 1. Read & clean ------------------------------------------------------
  raw_data_reactive <- reactive({
    req(input$data_file)
    ext <- tools::file_ext(input$data_file$name)
    validate(need(ext == "xlsx", "Please upload an .xlsx file."))

    df <- read_excel(input$data_file$datapath, sheet = 1)

    required_cols <- c("OriginDate", "PaymentDate", "ClaimAmount", "SubCat")
    validate(need(all(required_cols %in% names(df)),
                  paste("Missing required columns. Need:",
                        paste(required_cols, collapse = ", "))))

    out <- tryCatch({
      df %>%
        mutate(
          OriginDate  = as.Date(OriginDate),
          PaymentDate = as.Date(PaymentDate),
          ClaimAmount = as.numeric(ClaimAmount),
          SubCat      = as.factor(SubCat)
        ) %>%
        filter(!is.na(OriginDate), !is.na(PaymentDate), !is.na(ClaimAmount),
               PaymentDate >= OriginDate)
    }, error = function(e) {
      validate(paste("Error processing data:", e$message)); NULL
    })

    validate(need(!is.null(out) && nrow(out) > 0,
                  "No valid rows after cleaning."))
    out
  })

  # ---- 2. Dynamic UI --------------------------------------------------------
  output$subcat_selector_ui <- renderUI({
    df <- raw_data_reactive(); req(df)
    choices <- c("All Categories", sort(levels(df$SubCat)))
    selectInput("subcat_filter", "Subcategory", choices = choices)
  })

  output$download_button_ui <- renderUI({
    req(analysis_results())
    downloadButton("download_results", "Download results (.xlsx)",
                   icon = icon("download"), class = "btn-outline-primary w-100")
  })

  # ---- Exposure / earned premium upload (optional) --------------------------
  # Returns a tidy frame (Period, PeriodDate, EarnedPremium, Exposure) or NULL.
  # Parse errors surface as a notification rather than crashing the app.
  exposure_reactive <- reactive({
    if (is.null(input$exposure_file)) return(NULL)
    ext <- tools::file_ext(input$exposure_file$name)
    if (!identical(ext, "xlsx")) {
      showNotification("Exposure file must be .xlsx - ignoring it.",
                       type = "warning", duration = 6)
      return(NULL)
    }
    tryCatch(
      read_exposure_table(input$exposure_file$datapath),
      error = function(e) {
        showNotification(paste("Exposure file problem:", conditionMessage(e)),
                         type = "warning", duration = 8)
        NULL
      })
  })


  output$vb_rows  <- renderText({ df <- raw_data_reactive(); req(df)
    format(nrow(df), big.mark = ",") })
  output$vb_total <- renderText({ df <- raw_data_reactive(); req(df)
    paste0(format(round(sum(df$ClaimAmount)), big.mark = ",")) })
  output$vb_span  <- renderText({ df <- raw_data_reactive(); req(df)
    paste(format(min(df$OriginDate)), "->", format(max(df$OriginDate))) })

  # ---- 3. Core analysis (button-triggered) ----------------------------------
  analysis_results <- eventReactive(input$run_analysis, {
    req(raw_data_reactive(), input$subcat_filter)
    showNotification("Running analysis...", type = "message", duration = 2)

    df_raw <- raw_data_reactive()
    sel    <- input$subcat_filter
    df_f   <- if (sel != "All Categories")
                df_raw %>% filter(SubCat == sel) else df_raw

    validate(need(nrow(df_f) > 0,
                  paste("No data for subcategory:", sel)))

    # Build triangles
    long_tri <- build_long_triangle(df_f, period = input$period)
    incr_m   <- long_to_incr_matrix(long_tri)
    cum_m    <- incr_to_cum(incr_m)

    validate(need(nrow(cum_m) > 1,
                  "Chain Ladder needs >= 2 origin periods."))
    validate(need(ncol(cum_m) > 1,
                  "Chain Ladder needs >= 2 development periods."))

    # ---- Exposure / earned premium alignment ----
    # Align the (optional) uploaded exposure table to the triangle's origin
    # rows. exp_al is NULL when no usable file was supplied, in which case all
    # methods keep their original data-only behaviour.
    exp_al <- NULL
    ep_vec <- NULL; exposure_vec <- NULL
    ex_df  <- exposure_reactive()
    if (!is.null(ex_df)) {
      exp_al <- tryCatch(
        align_exposure(ex_df, cum_m, period = input$period),
        error = function(e) {
          showNotification(paste("Exposure alignment:", conditionMessage(e)),
                           type = "warning", duration = 6); NULL })
      if (!is.null(exp_al)) {
        ep_vec       <- exp_al$earned_premium
        exposure_vec <- exp_al$exposure
        if (exp_al$matched == 0) {
          showNotification(paste("Exposure file loaded but no periods matched the",
                                 "triangle - check the Period column. Using data-only",
                                 "estimates."), type = "warning", duration = 8)
          ep_vec <- NULL; exposure_vec <- NULL; exp_al <- NULL
        } else if (exp_al$matched < exp_al$n_origins) {
          showNotification(sprintf(
            "Exposure matched %d of %d origin periods; unmatched periods use the data-only estimate.",
            exp_al$matched, exp_al$n_origins), type = "message", duration = 6)
        }
      }
    }

    # ---- Mack ----
    mack <- tryCatch(run_mack(cum_m, tail = input$tail),
                     error = function(e) {
                       validate(paste("Mack error:", e$message)); NULL })
    req(mack)
    cl_ultimate <- mack$by_origin %>% filter(Origin != "Total") %>% pull(Ultimate)

    # ---- Bootstrap ----
    boot <- tryCatch(
      run_bootstrap(cum_m, n_sims = input$n_sims,
                    process_dist = input$boot_dist),
      error = function(e) { showNotification(
        paste("Bootstrap skipped:", e$message), type = "warning"); NULL })

    # ---- BF a-priori (exposure-based if premium supplied, else CL-seeded) ----
    if (!is.null(ep_vec)) {
      ea_bf <- exposure_apriori(cum_m, mack$dev_factors, ep_vec)
      bf <- tryCatch(run_bf(cum_m, apriori_ultimate = ea_bf$apriori),
                     error = function(e) NULL)
      bf_label <- "Bornhuetter-Ferguson (premium a-priori)"
    } else {
      bf <- tryCatch(run_bf(cum_m, apriori_ultimate = cl_ultimate),
                     error = function(e) NULL)
      bf_label <- "Bornhuetter-Ferguson (CL a-priori)"
    }

    # ---- Cape Cod (exposure-based if exposure supplied, else equal-exposure) ----
    capecod <- tryCatch(
      run_capecod(cum_m, mack$dev_factors, exposure = exposure_vec),
      error = function(e) NULL)

    # ---- Method comparison ----
    total_mack <- mack$by_origin %>% filter(Origin == "Total") %>% pull(IBNR)
    method_cmp <- tibble(
      Method = "Mack Chain Ladder",
      Total.IBNR = total_mack,
      Std.Err = mack$by_origin %>% filter(Origin == "Total") %>% pull(Mack.S.E)
    )
    if (!is.null(boot)) method_cmp <- bind_rows(method_cmp, tibble(
      Method = paste0("Bootstrap ODP (", input$boot_dist, ")"),
      Total.IBNR = mean(boot$sim_totals), Std.Err = sd(boot$sim_totals)))
    if (!is.null(bf)) method_cmp <- bind_rows(method_cmp, tibble(
      Method = bf_label,
      Total.IBNR = sum(bf$IBNR), Std.Err = NA_real_))
    if (!is.null(capecod)) method_cmp <- bind_rows(method_cmp, tibble(
      Method = if (!is.null(exposure_vec)) "Cape Cod (exposure ELR)"
               else "Cape Cod (equal-exposure)",
      Total.IBNR = sum(capecod$IBNR), Std.Err = NA_real_))

    # ---- Recast ----
    # Return either the analysis (list) or a list(error = "message") so the
    # Recast tab can explain itself instead of silently going blank.
    recast <- tryCatch(
      recast_analysis(cum_m, n_holdback = input$n_holdback),
      error = function(e) {
        showNotification(paste("Recast:", e$message), type = "warning",
                         duration = 6)
        list(error = e$message)
      })

    # ---- Seasonality ----
    seas <- tryCatch(seasonality_analysis(df_f), error = function(e) NULL)

    # ---- Blended / credibility-weighted estimate ----
    # Build a month-keyed seasonal index vector (payment-month basis) to tilt
    # the prior, and the per-origin dates so monthly/quarterly tilt aligns.
    blend <- tryCatch({
      seas_vec <- NULL
      if (isTRUE(input$blend_seasonal) && !is.null(seas)) {
        pay_idx <- seas$index %>% filter(Basis == "Payment month")
        # named numeric, position = month number 1..12
        v <- rep(NA_real_, 12)
        mi <- match(as.character(pay_idx$Mnth), month.abb)
        v[mi] <- pay_idx$Index
        v[is.na(v)] <- 100               # months with no data -> neutral
        seas_vec <- v
      }
      origin_dates <- as.Date(rownames(cum_m))
      run_blend(cum_m, mack$model, seas_index = seas_vec,
                period = input$period, origin_dates = origin_dates,
                kappa = input$blend_kappa, prior = input$blend_prior,
                earned_premium = ep_vec, exposure = exposure_vec)
    }, error = function(e) {
      showNotification(paste("Blend skipped:", e$message), type = "warning",
                       duration = 6); NULL })

    # Output-ready triangle frames
    incr_out <- as.data.frame(incr_m) %>% rownames_to_column("Origin")
    cum_out  <- as.data.frame(cum_m)  %>% rownames_to_column("Origin")
    dev_tbl  <- tibble(
      Transition = paste0("Dev", seq_along(mack$dev_factors),
                          "->", seq_along(mack$dev_factors) + 1),
      Factor = as.numeric(mack$dev_factors))

    list(
      sel = sel, df_f = df_f,
      incr_df = incr_out, cum_df = cum_out, dev_df = dev_tbl,
      mack = mack, boot = boot, bf = bf, capecod = capecod,
      reserve_df = mack$by_origin, method_cmp = method_cmp,
      recast = recast, seas = seas, blend = blend,
      exp_al = exp_al, ep_vec = ep_vec, exposure_vec = exposure_vec
    )
  })

  # ---- 4. Render outputs ----------------------------------------------------

  output$raw_data_table <- renderDT({
    req(raw_data_reactive())
    datatable(raw_data_reactive(),
              options = list(scrollX = TRUE, pageLength = 10))
  })

  # ---- Exposure & premium panel ---------------------------------------------
  output$exposure_status <- renderUI({
    ex <- exposure_reactive()
    if (is.null(ex)) {
      return(div(class = "alert alert-secondary",
        tags$b("No exposure / earned premium loaded. "),
        paste("Upload an .xlsx with columns Period, EarnedPremium, Exposure in",
              "the sidebar to switch the Bornhuetter-Ferguson, Benktander and",
              "Cape Cod methods onto a proper exposure base. Without it, those",
              "methods fall back to data-only (CL-seeded / equal-exposure)",
              "assumptions.")))
    }
    r <- tryCatch(analysis_results(), error = function(e) NULL)
    if (is.null(r) || is.null(r$exp_al)) {
      return(div(class = "alert alert-info",
        tags$b("Exposure file loaded. "),
        sprintf("%d period(s) read. Press Run analysis to align it to the triangle and apply the exposure-based methods.",
                nrow(ex))))
    }
    div(class = "alert alert-success",
        tags$b("Exposure applied. "),
        sprintf("%d of %d origin periods matched. Earned premium drives the BF / Benktander a-priori; exposure drives the Cape Cod ELR.",
                r$exp_al$matched, r$exp_al$n_origins))
  })

  output$exposure_table <- renderDT({
    ex <- exposure_reactive()
    r  <- tryCatch(analysis_results(), error = function(e) NULL)
    # Prefer the aligned (triangle-row) view once analysis has run.
    if (!is.null(r) && !is.null(r$exp_al)) {
      d <- r$exp_al$aligned
      datatable(d, rownames = FALSE,
                options = list(scrollX = TRUE, dom = "t",
                               pageLength = nrow(d))) %>%
        formatRound(c("EarnedPremium", "Exposure"), digits = 0)
    } else {
      req(ex)
      d <- ex %>% select(Period, EarnedPremium, Exposure)
      datatable(d, rownames = FALSE,
                options = list(scrollX = TRUE, pageLength = 15)) %>%
        formatRound(c("EarnedPremium", "Exposure"), digits = 0)
    }
  })

  output$vb_exp_matched <- renderText({
    r <- tryCatch(analysis_results(), error = function(e) NULL)
    if (is.null(r) || is.null(r$exp_al)) return("-")
    sprintf("%d / %d", r$exp_al$matched, r$exp_al$n_origins)
  })
  output$vb_exp_premium <- renderText({
    ex <- exposure_reactive(); if (is.null(ex)) return("-")
    format(round(sum(ex$EarnedPremium, na.rm = TRUE)), big.mark = ",")
  })
  output$vb_exp_exposure <- renderText({
    ex <- exposure_reactive(); if (is.null(ex)) return("-")
    format(round(sum(ex$Exposure, na.rm = TRUE)), big.mark = ",")
  })

  output$exposure_elr_table <- renderDT({
    r <- analysis_results(); req(r, r$blend)
    req(isTRUE(r$blend$has_exposure))
    tab <- tibble(
      Basis = c("Earned premium (BF / Benktander)", "Exposure (Cape Cod)"),
      `A-priori loss ratio` = c(r$blend$elr_premium, r$blend$elr_capecod)
    ) %>% filter(is.finite(`A-priori loss ratio`))
    validate(need(nrow(tab) > 0,
                  "Loss ratios become available once an exposure file is matched."))
    datatable(tab, rownames = FALSE, options = list(dom = "t")) %>%
      formatPercentage("A-priori loss ratio", digits = 1)
  })

  output$cum_caption  <- renderText({ r <- analysis_results(); req(r)
    paste("Cumulative triangle —", r$sel) })
  output$incr_caption <- renderText({ r <- analysis_results(); req(r)
    paste("Incremental triangle —", r$sel) })

  output$cumulative_triangle_table <- renderDT({
    r <- analysis_results(); req(r)
    num <- names(r$cum_df)[sapply(r$cum_df, is.numeric)]
    datatable(r$cum_df, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(r$cum_df))) %>%
      formatRound(columns = num, digits = 0)
  })

  output$incremental_triangle_table <- renderDT({
    r <- analysis_results(); req(r)
    num <- names(r$incr_df)[sapply(r$incr_df, is.numeric)]
    datatable(r$incr_df, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(r$incr_df))) %>%
      formatRound(columns = num, digits = 0)
  })

  output$dev_factor_table <- renderDT({
    r <- analysis_results(); req(r)
    datatable(r$dev_df, rownames = FALSE,
              options = list(dom = "t", pageLength = nrow(r$dev_df))) %>%
      formatRound(columns = "Factor", digits = 4)
  })

  output$reserve_estimates_table <- renderDT({
    r <- analysis_results(); req(r)
    datatable(r$reserve_df, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(r$reserve_df))) %>%
      formatRound(c("Latest", "Ultimate", "IBNR", "Mack.S.E"), digits = 0) %>%
      formatPercentage("CV", digits = 1) %>%
      formatPercentage("Dev.To.Date", digits = 1)
  })

  output$method_compare_table <- renderDT({
    r <- analysis_results(); req(r)
    datatable(r$method_cmp, rownames = FALSE,
              options = list(dom = "t", pageLength = nrow(r$method_cmp))) %>%
      formatRound(c("Total.IBNR", "Std.Err"), digits = 0)
  })

  # ---- Bootstrap ----
  output$boot_hist <- renderPlot({
    r <- analysis_results(); req(r, r$boot)
    df <- tibble(IBNR = r$boot$sim_totals)
    q  <- quantile(df$IBNR, c(.5, .75, .95))
    ggplot(df, aes(IBNR)) +
      geom_histogram(bins = 50, fill = "#2C3E50", alpha = .85) +
      geom_vline(xintercept = q, linetype = "dashed", colour = "#E74C3C") +
      annotate("text", x = q, y = Inf, label = c("50%", "75%", "95%"),
               vjust = 2, hjust = -0.1, colour = "#E74C3C", size = 3.5) +
      scale_x_continuous(labels = label_comma()) +
      labs(x = "Total IBNR", y = "Simulations")
  })

  output$boot_quantile_table <- renderDT({
    r <- analysis_results(); req(r, r$boot)
    datatable(r$boot$quantiles, rownames = FALSE,
              options = list(dom = "t", pageLength = nrow(r$boot$quantiles))) %>%
      formatRound("IBNR", digits = 0)
  })

  output$boot_origin_table <- renderDT({
    r <- analysis_results(); req(r, r$boot)
    df <- r$boot$by_origin
    num <- names(df)[sapply(df, is.numeric)]
    datatable(df, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t", pageLength = nrow(df))) %>%
      formatRound(num, digits = 0)
  })

  # ---- Blended estimate ----
  output$blend_detail_table <- renderDT({
    r <- analysis_results(); req(r, r$blend)
    d <- r$blend$detail
    # Drop the exposure columns when no exposure file was supplied (all NA).
    if (!isTRUE(r$blend$has_exposure))
      d <- d %>% select(-any_of(c("EarnedPrem", "Exposure")))
    round_cols <- intersect(
      c("Latest", "EarnedPrem", "Exposure", "CL", "Expected", "BF",
        "Benktander", "CapeCod", "Blended", "IBNR.CL", "IBNR.Blend"),
      names(d))
    datatable(d, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t", pageLength = nrow(d))) %>%
      formatRound(round_cols, digits = 0) %>%
      formatPercentage(c("Pct.Dev", "Credibility"), digits = 1)
  })

  output$blend_totals_table <- renderDT({
    r <- analysis_results(); req(r, r$blend)
    datatable(r$blend$totals, rownames = FALSE,
              options = list(dom = "t", pageLength = nrow(r$blend$totals))) %>%
      formatRound(c("Total.Ultimate", "Total.IBNR"), digits = 0)
  })

  output$blend_curve_plot <- renderPlot({
    r <- analysis_results(); req(r, r$blend)
    d <- r$blend$methods_long %>%
      mutate(Origin = factor(Origin, levels = unique(r$blend$detail$Origin)),
             Highlight = Method == "Blended")
    ggplot(d, aes(Origin, Ultimate, group = Method, colour = Method)) +
      geom_line(aes(linewidth = Highlight)) +
      geom_point(data = filter(d, Highlight), size = 2) +
      scale_linewidth_manual(values = c(`FALSE` = 0.6, `TRUE` = 1.6),
                             guide = "none") +
      scale_colour_manual(values = c(
        CL = "#E74C3C", Expected = "#27AE60", BF = "#8E44AD",
        Benktander = "#E67E22", CapeCod = "#16A085", Blended = "#2C3E50")) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "Estimated ultimate", colour = NULL,
           subtitle = "Methods agree at maturity (left) and diverge for green periods (right)") +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  })

  output$blend_credibility_plot <- renderPlot({
    r <- analysis_results(); req(r, r$blend)
    d <- r$blend$detail %>%
      mutate(Origin = factor(Origin, levels = Origin))
    ggplot(d, aes(Pct.Dev, Credibility)) +
      geom_line(colour = "#95A5A6") +
      geom_point(aes(colour = Credibility), size = 3) +
      scale_colour_gradient(low = "#E74C3C", high = "#2C3E50", guide = "none") +
      scale_x_continuous(labels = label_percent()) +
      scale_y_continuous(labels = label_percent(), limits = c(0, 1)) +
      labs(x = "Maturity (% developed)",
           y = "Credibility Z (weight on own data)",
           subtitle = sprintf("Z = (%% developed)^kappa, kappa = %.1f",
                              r$blend$kappa))
  })

  output$blend_ibnr_plot <- renderPlot({
    r <- analysis_results(); req(r, r$blend)
    d <- r$blend$detail %>%
      select(Origin, `Chain Ladder` = IBNR.CL, Blended = IBNR.Blend) %>%
      mutate(Origin = factor(Origin, levels = Origin)) %>%
      pivot_longer(c(`Chain Ladder`, Blended),
                   names_to = "Method", values_to = "IBNR")
    ggplot(d, aes(Origin, IBNR, fill = Method)) +
      geom_col(position = "dodge") +
      scale_fill_manual(values = c(`Chain Ladder` = "#E74C3C",
                                   Blended = "#2C3E50")) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "IBNR reserve", fill = NULL,
           subtitle = "Identical for mature origins; blend tames the volatile newest periods") +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  })

  # ---- Mack diagnostics ----
  render_mack <- function(which_n) renderPlot({
    r <- analysis_results(); req(r, r$mack$model)
    old <- getOption("scipen"); on.exit(options(scipen = old), add = TRUE)
    options(scipen = 999)
    plot(r$mack$model, which = which_n)
  })
  output$mack_plot1 <- render_mack(1)
  output$mack_plot2 <- render_mack(2)

  # ---- Recast ----
  # A recast result is valid only when it has a $detail frame; otherwise it
  # carries $error with a human-readable reason.
  recast_ok <- reactive({
    r <- analysis_results(); req(r)
    rc <- r$recast
    if (is.null(rc) || !is.null(rc$error) || is.null(rc$detail)) return(NULL)
    rc
  })

  output$recast_status <- renderUI({
    r <- analysis_results(); req(r)
    rc <- r$recast
    if (is.null(rc)) return(NULL)
    if (!is.null(rc$error)) {
      div(class = "alert alert-warning",
          tags$b("Recast could not run. "), rc$error)
    } else {
      div(class = "alert alert-success",
          sprintf("Back-test complete: %d held-back cells compared.",
                  nrow(rc$detail)))
    }
  })

  output$recast_summary_table <- renderDT({
    rc <- recast_ok(); req(rc)
    s <- rc$summary
    datatable(s, rownames = FALSE, options = list(dom = "t")) %>%
      formatRound(c("Total.Actual", "Total.Expected", "Total.Diff", "RMSE"),
                  digits = 0) %>%
      formatPercentage(c("Total.Pct", "MAPE"), digits = 1)
  })

  output$recast_detail_table <- renderDT({
    rc <- recast_ok(); req(rc)
    d <- rc$detail
    datatable(d, rownames = FALSE,
              options = list(scrollX = TRUE, pageLength = 15)) %>%
      formatRound(c("Actual", "Expected", "AvE_Diff"), digits = 0) %>%
      formatPercentage("AvE_Pct", digits = 1)
  })

  output$recast_plot <- renderPlot({
    rc <- recast_ok(); req(rc)
    d <- rc$detail %>%
      group_by(Origin) %>%
      summarise(Actual = sum(Actual), Expected = sum(Expected),
                .groups = "drop") %>%
      pivot_longer(c(Actual, Expected), names_to = "Type", values_to = "Val")
    ggplot(d, aes(Origin, Val, fill = Type)) +
      geom_col(position = "dodge") +
      scale_fill_manual(values = c(Actual = "#2C3E50", Expected = "#95A5A6")) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "Held-back cumulative", fill = NULL) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  })

  # ---- Seasonality ----
  output$seasonality_plot <- renderPlot({
    r <- analysis_results(); req(r, r$seas)
    ggplot(r$seas$index, aes(Mnth, Index, group = Basis, colour = Basis)) +
      geom_hline(yintercept = 100, linetype = "dotted") +
      geom_line(linewidth = 1) + geom_point(size = 2) +
      scale_colour_manual(values = c("Origin month" = "#2C3E50",
                                     "Payment month" = "#E67E22")) +
      labs(x = NULL, y = "Index (100 = avg)", colour = NULL)
  })

  output$seasonality_heat <- renderPlot({
    r <- analysis_results(); req(r, r$seas)
    ggplot(r$seas$heat, aes(Mnth, Yr, fill = Amount)) +
      geom_tile(colour = "white") +
      scale_fill_gradient(low = "#EAF2F8", high = "#2C3E50",
                          labels = label_comma()) +
      labs(x = NULL, y = NULL, fill = "Paid")
  })

  output$seasonality_table <- renderDT({
    r <- analysis_results(); req(r, r$seas)
    tab <- r$seas$index %>%
      select(Basis, Mnth, MeanAmount, Index) %>%
      arrange(Basis, Mnth)
    datatable(tab, rownames = FALSE,
              options = list(scrollX = TRUE, pageLength = 24, dom = "t")) %>%
      formatRound("MeanAmount", digits = 0) %>%
      formatRound("Index", digits = 1)
  })

  # ==========================================================================
  #  ACTUARY ADJUSTMENTS (judgemental overlay on the model output)
  # --------------------------------------------------------------------------
  #  adj_state holds one row per origin: the selection Basis, a percentage
  #  load, a dollar add-on, an optional hard Override ultimate, a Comment and
  #  the last-edit timestamp. It is (re)initialised whenever the analysis is
  #  re-run, because prior overlays no longer refer to the same model.
  # ==========================================================================

  ADJ_BASES <- c("Blended", "CL", "BF", "Benktander", "CapeCod", "Expected")
  ADJ_COLS  <- c("Origin", "Latest", "Pct.Dev", "Basis", "Base.Ultimate",
                 "AdjPct", "AdjAmt", "Override", "Selected.Ultimate",
                 "Selected.IBNR", "Comment")

  adj_state <- reactiveVal(NULL)

  # Per-origin ultimates for every candidate estimator, in triangle-row order.
  method_ultimates <- reactive({
    r <- analysis_results(); req(r)
    if (!is.null(r$blend)) {
      d <- r$blend$detail
      tibble(Origin = d$Origin, Latest = d$Latest, Pct.Dev = d$Pct.Dev,
             CL = d$CL, Expected = d$Expected, BF = d$BF,
             Benktander = d$Benktander, CapeCod = d$CapeCod,
             Blended = d$Blended)
    } else {
      d <- r$reserve_df %>% filter(Origin != "Total")
      tibble(Origin = d$Origin, Latest = d$Latest, Pct.Dev = d$Dev.To.Date,
             CL = d$Ultimate, Expected = NA_real_, BF = NA_real_,
             Benktander = NA_real_, CapeCod = NA_real_, Blended = d$Ultimate)
    }
  })

  default_basis <- reactive({
    r <- analysis_results(); req(r)
    if (!is.null(r$blend)) "Blended" else "CL"
  })

  observeEvent(analysis_results(), {
    mu <- method_ultimates(); req(mu)
    prev <- adj_state()
    adj_state(tibble(
      Origin  = mu$Origin,
      Basis   = default_basis(),
      AdjPct  = 0,
      AdjAmt  = 0,
      Override = NA_real_,
      Comment = "",
      Edited  = NA_character_
    ))
    if (!is.null(prev) && any(prev$AdjPct != 0 | prev$AdjAmt != 0 |
                              is.finite(prev$Override) |
                              nzchar(prev$Comment), na.rm = TRUE)) {
      showNotification(paste("Analysis re-run: the adjustment worksheet was",
                             "re-initialised and previous overlays cleared."),
                       type = "warning", duration = 6)
    }
  })

  # The single source of truth for the booked position.
  adjusted_reserves <- reactive({
    st <- adj_state(); mu <- method_ultimates()
    req(st, mu)
    def <- default_basis()

    df <- mu %>% left_join(st, by = "Origin") %>%
      mutate(
        Basis   = ifelse(is.na(Basis) | !(Basis %in% ADJ_BASES), def, Basis),
        AdjPct  = coalesce(AdjPct, 0),
        AdjAmt  = coalesce(AdjAmt, 0),
        Comment = coalesce(Comment, "")
      )

    base_ult <- vapply(seq_len(nrow(df)), function(i) {
      v <- df[[df$Basis[i]]][i]
      if (!is.finite(v)) v <- df$Blended[i]        # fallback chain
      if (!is.finite(v)) v <- df$CL[i]
      v
    }, numeric(1))

    selected <- ifelse(is.finite(df$Override) & df$Override > 0,
                       df$Override,
                       base_ult * (1 + df$AdjPct / 100) + df$AdjAmt)

    tibble(
      Origin            = df$Origin,
      Latest            = df$Latest,
      Pct.Dev           = df$Pct.Dev,
      Basis             = df$Basis,
      Base.Ultimate     = base_ult,
      AdjPct            = df$AdjPct,
      AdjAmt            = df$AdjAmt,
      Override          = df$Override,
      Selected.Ultimate = selected,
      Selected.IBNR     = selected - df$Latest,
      Model.IBNR        = base_ult - df$Latest,
      Judgement         = selected - base_ult,
      CL.IBNR           = df$CL - df$Latest,
      Comment           = df$Comment,
      Edited            = df$Edited
    )
  })

  # ---- Editable worksheet ----------------------------------------------------
  output$adj_table <- renderDT({
    ar <- adjusted_reserves(); req(ar)
    d <- ar %>% select(all_of(ADJ_COLS))
    # 0-based indices of the non-editable (computed) columns
    lock <- which(ADJ_COLS %in% c("Origin", "Latest", "Pct.Dev",
                                  "Base.Ultimate", "Selected.Ultimate",
                                  "Selected.IBNR")) - 1L
    datatable(d, rownames = FALSE,
              editable = list(target = "cell", disable = list(columns = lock)),
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(d))) %>%
      formatRound(c("Latest", "Base.Ultimate", "AdjAmt", "Override",
                    "Selected.Ultimate", "Selected.IBNR"), digits = 0) %>%
      formatRound("AdjPct", digits = 1) %>%
      formatPercentage("Pct.Dev", digits = 1) %>%
      formatStyle("Selected.IBNR",
                  fontWeight = "bold",
                  color = styleInterval(0, c("#B03A2E", "#2C3E50")))
  })

  observeEvent(input$adj_table_cell_edit, {
    info <- input$adj_table_cell_edit
    st <- adj_state(); req(st)
    i  <- info$row
    if (is.null(i) || i < 1 || i > nrow(st)) return()
    cn  <- ADJ_COLS[info$col + 1L]
    val <- info$value

    if (cn == "Basis") {
      v   <- trimws(as.character(val))
      hit <- ADJ_BASES[tolower(ADJ_BASES) == tolower(v)]
      if (!length(hit)) {
        showNotification(paste0("'", v, "' is not a valid basis. Use one of: ",
                                paste(ADJ_BASES, collapse = ", "), "."),
                         type = "error", duration = 5)
        adj_state(st)   # bump reactivity so the table redraws the old value
        return()
      }
      st$Basis[i] <- hit[1]
    } else if (cn %in% c("AdjPct", "AdjAmt", "Override")) {
      v <- suppressWarnings(as.numeric(gsub("[,%$ ]", "", as.character(val))))
      if (cn == "Override") {
        st$Override[i] <- if (is.finite(v) && v > 0) v else NA_real_
        if (!is.na(val) && nzchar(trimws(as.character(val))) &&
            !(is.finite(v) && v > 0))
          showNotification("Override must be a positive number; cleared instead.",
                           type = "warning", duration = 4)
      } else {
        if (!is.finite(v)) {
          showNotification("Please enter a number.", type = "error",
                           duration = 4)
          adj_state(st); return()
        }
        if (cn == "AdjPct" && abs(v) > 100)
          showNotification(paste("That is a very large percentage adjustment",
                                 "- double-check it was intended."),
                           type = "warning", duration = 5)
        st[[cn]][i] <- v
      }
    } else if (cn == "Comment") {
      st$Comment[i] <- as.character(val)
    } else return()

    st$Edited[i] <- format(Sys.time(), "%Y-%m-%d %H:%M:%S")
    adj_state(st)
  })

  observeEvent(input$adj_apply_basis, {
    st <- adj_state(); req(st)
    st$Basis  <- input$adj_basis_all
    st$Edited <- format(Sys.time(), "%Y-%m-%d %H:%M:%S")
    adj_state(st)
    showNotification(paste("Selection basis set to", input$adj_basis_all,
                           "for all origins."), type = "message", duration = 3)
  })

  observeEvent(input$adj_reset, {
    mu <- method_ultimates(); req(mu)
    adj_state(tibble(Origin = mu$Origin, Basis = default_basis(),
                     AdjPct = 0, AdjAmt = 0, Override = NA_real_,
                     Comment = "", Edited = NA_character_))
    showNotification("All adjustments reset to the model position.",
                     type = "message", duration = 3)
  })

  # ---- Live totals & guardrails ---------------------------------------------
  fmt_money <- function(x) {
    if (is.null(x) || length(x) == 0 || !is.finite(x)) return("n/a")
    format(round(x), big.mark = ",", scientific = FALSE)
  }

  output$vb_adj_base <- renderText({
    ar <- adjusted_reserves(); req(ar); fmt_money(sum(ar$Model.IBNR)) })
  output$vb_adj_final <- renderText({
    ar <- adjusted_reserves(); req(ar); fmt_money(sum(ar$Selected.IBNR)) })
  output$vb_adj_delta <- renderText({
    ar <- adjusted_reserves(); req(ar)
    d <- sum(ar$Judgement)
    paste0(ifelse(d >= 0, "+", "-"), fmt_money(abs(d))) })

  output$adj_guardrail <- renderUI({
    ar <- adjusted_reserves(); req(ar)
    r  <- tryCatch(analysis_results(), error = function(e) NULL)
    msgs <- tagList()

    neg <- ar %>% filter(Selected.Ultimate < Latest)
    if (nrow(neg)) {
      msgs <- tagList(msgs, div(class = "alert alert-danger",
        tags$b("Selected ultimate below paid-to-date: "),
        paste(neg$Origin, collapse = ", "),
        paste(". This implies a negative reserve (net recoveries). Confirm",
              "this is intended before booking.")))
    }

    over <- ar %>% filter(is.finite(Override) & Override > 0 &
                            (is.na(Comment) | !nzchar(Comment)))
    if (nrow(over)) {
      msgs <- tagList(msgs, div(class = "alert alert-warning",
        tags$b("Overrides without a rationale: "),
        paste(over$Origin, collapse = ", "),
        ". Add a Comment so the audit log explains the judgement."))
    }

    if (!is.null(r) && !is.null(r$boot)) {
      tot <- sum(ar$Selected.IBNR)
      lo  <- quantile(r$boot$sim_totals, 0.05)
      hi  <- quantile(r$boot$sim_totals, 0.95)
      if (is.finite(tot) && (tot < lo || tot > hi)) {
        msgs <- tagList(msgs, div(class = "alert alert-warning",
          tags$b("Outside the simulated range. "),
          sprintf(paste("The booked total IBNR (%s) falls outside the",
                        "bootstrap 5%%-95%% interval (%s to %s). Large",
                        "judgemental positions warrant explicit documentation",
                        "in the sign-off."),
                  fmt_money(tot), fmt_money(lo), fmt_money(hi))))
      } else if (is.finite(tot)) {
        msgs <- tagList(msgs, div(class = "alert alert-success",
          sprintf(paste("The booked total IBNR (%s) sits within the simulated",
                        "5%%-95%% range (%s to %s)."),
                  fmt_money(tot), fmt_money(lo), fmt_money(hi))))
      }
    }
    msgs
  })

  output$adj_compare_plot <- renderPlot({
    ar <- adjusted_reserves(); req(ar)
    d <- ar %>%
      select(Origin, Model = Model.IBNR, Booked = Selected.IBNR, Override) %>%
      mutate(Origin = factor(Origin, levels = Origin)) %>%
      pivot_longer(c(Model, Booked), names_to = "View", values_to = "IBNR") %>%
      mutate(View = factor(View, levels = c("Model", "Booked")))
    ov <- ar %>% filter(is.finite(Override) & Override > 0) %>%
      mutate(Origin = factor(Origin, levels = levels(d$Origin)))
    p <- ggplot(d, aes(Origin, IBNR, fill = View)) +
      geom_col(position = "dodge") +
      scale_fill_manual(values = c(Model = "#95A5A6", Booked = "#2C3E50")) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "IBNR reserve", fill = NULL,
           subtitle = "Grey = model position; dark = after actuarial adjustment") +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
    if (nrow(ov))
      p <- p + geom_point(data = ov,
                          aes(Origin, Selected.IBNR), inherit.aes = FALSE,
                          shape = 8, size = 3, colour = "#B03A2E")
    p
  })

  # ---- Final Summary tab ------------------------------------------------------
  output$final_headline_ui <- renderUI({
    ar <- adjusted_reserves(); req(ar)
    r  <- tryCatch(analysis_results(), error = function(e) NULL)

    booked  <- sum(ar$Selected.IBNR)
    model   <- sum(ar$Model.IBNR)
    cl_tot  <- sum(ar$CL.IBNR, na.rm = TRUE)
    judged  <- sum(ar$Judgement)
    n_adj   <- sum(abs(ar$Judgement) > 1e-9)

    pctl_txt <- NULL
    if (!is.null(r) && !is.null(r$boot) && is.finite(booked)) {
      pctl <- mean(r$boot$sim_totals <= booked)
      pctl_txt <- sprintf("%.0f%% percentile of simulation", 100 * pctl)
    }

    box <- function(title, value, sub, accent = "#2C3E50") {
      div(style = sprintf(
        "flex:1; min-width:200px; background:#fff; border-top:4px solid %s; border-radius:6px; padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,.08);",
        accent),
        div(style = "font-size:.8em; text-transform:uppercase; letter-spacing:.04em; color:#7F8C8D;", title),
        div(style = sprintf("font-size:1.6em; font-weight:700; color:%s;", accent), value),
        div(style = "font-size:.85em; color:#566573; margin-top:2px;", sub))
    }

    div(style = "display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px;",
        box("Booked IBNR reserve", fmt_money(booked),
            pctl_txt %||% "Final post-adjustment figure", "#B03A2E"),
        box("Model IBNR (selected bases)", fmt_money(model),
            "Pre-adjustment position", "#2C3E50"),
        box("Judgemental overlay",
            paste0(ifelse(judged >= 0, "+", "-"), fmt_money(abs(judged))),
            sprintf("%d origin period(s) adjusted", n_adj), "#16A085"),
        box("vs pure Chain Ladder",
            paste0(ifelse(booked - cl_tot >= 0, "+", "-"),
                   fmt_money(abs(booked - cl_tot))),
            sprintf("CL total: %s", fmt_money(cl_tot)), "#8E44AD"))
  })

  output$final_waterfall <- renderPlot({
    ar <- adjusted_reserves(); req(ar)
    model_tot <- sum(ar$Model.IBNR)
    booked    <- sum(ar$Selected.IBNR)

    adj <- ar %>% filter(abs(Judgement) > 1e-9) %>%
      arrange(desc(abs(Judgement))) %>% select(Origin, Judgement)
    if (nrow(adj) > 8) {
      adj <- bind_rows(adj[1:7, ],
                       tibble(Origin = "Other adjustments",
                              Judgement = sum(adj$Judgement[8:nrow(adj)])))
    }

    steps <- bind_rows(
      tibble(Label = "Model estimate", Amount = model_tot, Type = "total"),
      if (nrow(adj)) adj %>%
        transmute(Label = Origin, Amount = Judgement,
                  Type = ifelse(Judgement >= 0, "up", "down")),
      tibble(Label = "Booked reserve", Amount = booked, Type = "total")
    ) %>% mutate(id = row_number())

    run <- 0; ymin <- ymax <- numeric(nrow(steps))
    for (k in seq_len(nrow(steps))) {
      if (steps$Type[k] == "total") {
        ymin[k] <- min(0, steps$Amount[k]); ymax[k] <- max(0, steps$Amount[k])
        run <- steps$Amount[k]
      } else {
        ymin[k] <- min(run, run + steps$Amount[k])
        ymax[k] <- max(run, run + steps$Amount[k])
        run <- run + steps$Amount[k]
      }
    }
    steps$ymin <- ymin; steps$ymax <- ymax

    sub <- if (nrow(adj) == 0)
      "No judgemental adjustments applied - booked equals the model estimate"
    else sprintf("Net judgemental overlay: %s%s",
                 ifelse(booked - model_tot >= 0, "+", "-"),
                 fmt_money(abs(booked - model_tot)))

    ggplot(steps) +
      geom_rect(aes(xmin = id - 0.38, xmax = id + 0.38,
                    ymin = ymin, ymax = ymax, fill = Type)) +
      geom_segment(data = steps[-nrow(steps), ],
                   aes(x = id + 0.38, xend = id + 0.62,
                       y = ifelse(Type == "total", ymax,
                                  ifelse(Type == "up", ymax, ymin)),
                       yend = ifelse(Type == "total", ymax,
                                     ifelse(Type == "up", ymax, ymin))),
                   linetype = "dotted", colour = "#7F8C8D") +
      scale_fill_manual(values = c(total = "#2C3E50", up = "#16A085",
                                   down = "#B03A2E"),
                        labels = c(total = "Position", up = "Increase",
                                   down = "Release"), name = NULL) +
      scale_x_continuous(breaks = steps$id, labels = steps$Label) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "Total IBNR", subtitle = sub) +
      theme(axis.text.x = element_text(angle = 30, hjust = 1))
  })

  output$final_ibnr_plot <- renderPlot({
    ar <- adjusted_reserves(); req(ar)
    d <- ar %>%
      select(Origin, `Chain Ladder` = CL.IBNR, `Model selection` = Model.IBNR,
             Booked = Selected.IBNR) %>%
      mutate(Origin = factor(Origin, levels = Origin)) %>%
      pivot_longer(-Origin, names_to = "View", values_to = "IBNR") %>%
      mutate(View = factor(View, levels = c("Chain Ladder", "Model selection",
                                            "Booked")))
    ggplot(d, aes(Origin, IBNR, fill = View)) +
      geom_col(position = "dodge") +
      scale_fill_manual(values = c(`Chain Ladder` = "#E74C3C",
                                   `Model selection` = "#95A5A6",
                                   Booked = "#2C3E50")) +
      scale_y_continuous(labels = label_comma()) +
      labs(x = NULL, y = "IBNR reserve", fill = NULL) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  })

  output$final_dist_plot <- renderPlot({
    r  <- tryCatch(analysis_results(), error = function(e) NULL)
    ar <- adjusted_reserves(); req(ar)
    validate(need(!is.null(r) && !is.null(r$boot),
                  "Bootstrap distribution unavailable for this run."))
    df <- tibble(IBNR = r$boot$sim_totals)
    booked <- sum(ar$Selected.IBNR)
    model  <- sum(ar$Model.IBNR)
    pctl   <- mean(df$IBNR <= booked)
    ggplot(df, aes(IBNR)) +
      geom_histogram(bins = 50, fill = "#BDC3C7", alpha = .9) +
      geom_vline(xintercept = model, colour = "#2C3E50",
                 linetype = "dashed", linewidth = 1) +
      geom_vline(xintercept = booked, colour = "#B03A2E", linewidth = 1.2) +
      annotate("text", x = model, y = Inf, label = "Model", vjust = 2,
               hjust = 1.1, colour = "#2C3E50", size = 3.5) +
      annotate("text", x = booked, y = Inf, label = "Booked", vjust = 3.5,
               hjust = -0.1, colour = "#B03A2E", size = 3.5) +
      scale_x_continuous(labels = label_comma()) +
      labs(x = "Total IBNR", y = "Simulations",
           subtitle = sprintf(
             "Booked reserve sits at the %.0f%% percentile of simulated outcomes",
             100 * pctl))
  })

  output$final_table <- renderDT({
    ar <- adjusted_reserves(); req(ar)
    d <- ar %>% select(Origin, Latest, Pct.Dev, Basis, Base.Ultimate,
                       AdjPct, AdjAmt, Override, Selected.Ultimate,
                       Selected.IBNR, Judgement, Comment)
    datatable(d, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(d))) %>%
      formatRound(c("Latest", "Base.Ultimate", "AdjAmt", "Override",
                    "Selected.Ultimate", "Selected.IBNR", "Judgement"),
                  digits = 0) %>%
      formatRound("AdjPct", digits = 1) %>%
      formatPercentage("Pct.Dev", digits = 1) %>%
      formatStyle("Judgement",
                  color = styleInterval(c(-1e-9, 1e-9),
                                        c("#B03A2E", "#7F8C8D", "#16A085")),
                  fontWeight = "bold")
  })

  output$adj_log_table <- renderDT({
    ar <- adjusted_reserves(); req(ar)
    d <- ar %>%
      filter(abs(Judgement) > 1e-9 | !is.na(Edited)) %>%
      select(Origin, Basis, AdjPct, AdjAmt, Override, Judgement,
             Comment, `Last edited` = Edited)
    validate(need(nrow(d) > 0,
                  "No adjustments have been made - the booked reserve equals the model position."))
    datatable(d, rownames = FALSE,
              options = list(scrollX = TRUE, dom = "t",
                             pageLength = nrow(d))) %>%
      formatRound(c("AdjAmt", "Override", "Judgement"), digits = 0) %>%
      formatRound("AdjPct", digits = 1)
  })

  # ---- Executive summary ----------------------------------------------------
  output$exec_summary_ui <- renderUI({
    r <- analysis_results(); req(r)

    money <- function(x) {
      if (is.null(x) || length(x) == 0 || !is.finite(x)) return("n/a")
      paste0(format(round(x), big.mark = ",", scientific = FALSE))
    }
    pct <- function(x, d = 1) {
      if (is.null(x) || length(x) == 0 || !is.finite(x)) return("n/a")
      paste0(formatC(100 * x, format = "f", digits = d), "%")
    }

    # --- Pull headline numbers -----------------------------------------------
    cl_total <- r$reserve_df %>% filter(Origin == "Total") %>% pull(IBNR)
    cl_se    <- r$reserve_df %>% filter(Origin == "Total") %>% pull(Mack.S.E)

    rec_total   <- NA_real_; rec_label <- "Chain Ladder"
    blend_delta <- NA_real_
    if (!is.null(r$blend)) {
      rec_total   <- r$blend$totals %>%
        filter(Method == "Blended (recommended)") %>% pull(Total.IBNR)
      rec_label   <- "Blended (credibility-weighted)"
      blend_delta <- rec_total - cl_total
    } else {
      rec_total <- cl_total
    }

    # Bootstrap risk band
    boot_50 <- boot_75 <- boot_95 <- NA_real_
    if (!is.null(r$boot)) {
      qs <- r$boot$quantiles
      getq <- function(lbl) { v <- qs %>% filter(Quantile == lbl) %>% pull(IBNR)
                              if (length(v)) v else NA_real_ }
      boot_50 <- getq("50%"); boot_75 <- getq("75%"); boot_95 <- getq("95%")
    }

    # Back-test reliability
    bt_mape <- bt_rmse <- bt_pct <- NA_real_; bt_ok <- FALSE
    if (!is.null(r$recast) && is.null(r$recast$error) &&
        !is.null(r$recast$detail)) {
      bt_ok   <- TRUE
      bt_mape <- r$recast$summary$MAPE
      bt_rmse <- r$recast$summary$RMSE
      bt_pct  <- r$recast$summary$Total.Pct
    }

    # Most uncertain origins (lowest maturity) from the blend detail
    green_txt <- "n/a"
    if (!is.null(r$blend)) {
      green <- r$blend$detail %>% arrange(Pct.Dev) %>% head(3)
      green_txt <- paste(sprintf("%s (%.0f%% developed)",
                                 green$Origin, 100 * green$Pct.Dev),
                         collapse = ", ")
    }

    # Seasonality signal (payment basis spread)
    seas_txt <- "Seasonality not available."
    if (!is.null(r$seas)) {
      pay <- r$seas$index %>% filter(Basis == "Payment month")
      if (nrow(pay)) {
        hi <- pay %>% slice_max(Index, n = 1)
        lo <- pay %>% slice_min(Index, n = 1)
        seas_txt <- sprintf(
          "Payments peak in %s (index %.0f) and trough in %s (index %.0f) - a %.0f-point swing around the average.",
          as.character(hi$Mnth), hi$Index, as.character(lo$Mnth), lo$Index,
          hi$Index - lo$Index)
      }
    }

    # Post-adjustment booked figure (if the actuary has applied overlays)
    booked_box_data <- NULL
    ar <- tryCatch(adjusted_reserves(), error = function(e) NULL)
    if (!is.null(ar)) {
      booked <- sum(ar$Selected.IBNR)
      if (is.finite(booked) && is.finite(rec_total) &&
          abs(booked - rec_total) > 0.5) {
        booked_box_data <- list(
          value = booked,
          sub = sprintf("Judgemental overlay %s%s vs recommended",
                        ifelse(booked >= rec_total, "+", "-"),
                        money(abs(booked - rec_total))))
      }
    }

    # --- Headline value boxes -------------------------------------------------
    box <- function(title, value, sub, accent = "#2C3E50") {
      div(style = sprintf(
        "flex:1; min-width:200px; background:#fff; border-top:4px solid %s; border-radius:6px; padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,.08);",
        accent),
        div(style = "font-size:.8em; text-transform:uppercase; letter-spacing:.04em; color:#7F8C8D;", title),
        div(style = sprintf("font-size:1.6em; font-weight:700; color:%s;", accent), value),
        div(style = "font-size:.85em; color:#566573; margin-top:2px;", sub))
    }

    headline <- div(
      style = "display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px;",
      box("Recommended reserve (IBNR)", money(rec_total), rec_label, "#2C3E50"),
      box("Chain Ladder reserve", money(cl_total),
          sprintf("Std. error +/- %s", money(cl_se)), "#E74C3C"),
      if (!is.na(boot_95))
        box("Stress level (95th pct)", money(boot_95),
            "Bootstrap adverse scenario", "#8E44AD"),
      if (!is.na(blend_delta))
        box("Blend vs CL", paste0(ifelse(blend_delta >= 0, "+", ""),
                                  money(blend_delta)),
            "Effect of stabilising green periods", "#16A085"),
      if (!is.null(booked_box_data))
        box("Booked (after adjustments)", money(booked_box_data$value),
            booked_box_data$sub, "#B03A2E")
    )

    # --- Key points -----------------------------------------------------------
    point <- function(head, body, accent = "#2C3E50") {
      div(style = sprintf(
        "border-left:4px solid %s; background:#F8F9FA; padding:10px 14px; margin-bottom:10px; border-radius:4px;",
        accent),
        tags$div(style = "font-weight:600; color:#2C3E50; margin-bottom:2px;", head),
        tags$div(style = "color:#34495E;", body))
    }

    pts <- tagList()

    # 1. Recommended number
    pts <- tagList(pts, point(
      "1. The number to book",
      HTML(sprintf(
        "Hold roughly <b>%s</b> in IBNR reserves on this basis. This is the %s estimate, which keeps standard Chain Ladder for mature periods but anchors the newest, most volatile periods to a stable prior. Plain Chain Ladder alone gives <b>%s</b>%s.",
        money(rec_total), rec_label, money(cl_total),
        if (!is.na(blend_delta) && abs(blend_delta) > 0)
          sprintf(" (a %s of %s)",
                  ifelse(blend_delta < 0, "reduction", "increase"),
                  money(abs(blend_delta))) else ""))))

    # 2. Why the blend / volatility
    pts <- tagList(pts, point(
      "2. Why we don't just use Chain Ladder",
      HTML(sprintf(
        "Chain Ladder is dependable for older periods but unstable for the newest ones, because it scales a tiny, noisy latest figure up by the full remaining development pattern. The least-developed periods here are <b>%s</b>. These carry most of the estimate's uncertainty and are where the blend does its work; treat their raw CL numbers with caution.",
        green_txt)), "#E67E22"))

    # 3. Risk range
    if (!is.na(boot_95)) {
      pts <- tagList(pts, point(
        "3. The range of outcomes (capital / risk margin)",
        HTML(sprintf(
          "Simulation puts the central estimate near <b>%s</b>, with a 1-in-4 adverse outcome around <b>%s</b> and a 1-in-20 adverse outcome around <b>%s</b>. The gap between the central and stress figures is the cushion to weigh when setting risk margin or capital.",
          money(boot_50), money(boot_75), money(boot_95))), "#8E44AD"))
    }

    # 4. Reliability / back-test
    if (bt_ok) {
      reliability <- if (is.na(bt_mape)) "unrated" else
        if (bt_mape < 0.05) "strong" else
        if (bt_mape < 0.15) "reasonable" else "weak"
      acc <- switch(reliability, strong = "#27AE60", reasonable = "#F39C12",
                    weak = "#E74C3C", "#95A5A6")
      pts <- tagList(pts, point(
        "4. How much to trust the model",
        HTML(sprintf(
          "Back-testing on recently held-back data shows <b>%s</b> reliability (average error %s, and a net bias of %s versus actuals). %s",
          reliability, pct(bt_mape), pct(bt_pct),
          if (reliability == "weak")
            "The model has missed recent actuals by a wide margin - lean harder on the blended/stabilised figure and consider expert overlay."
          else if (reliability == "reasonable")
            "The model tracks recent actuals acceptably; the headline numbers are usable with normal review."
          else
            "The model has predicted recent actuals closely, which supports confidence in the headline numbers.")),
        acc))
    } else {
      pts <- tagList(pts, point(
        "4. How much to trust the model",
        "The back-test could not run on this dataset (the triangle is too small once recent data is held back). Without it, treat the estimates as directional and prioritise the stabilised blend.",
        "#95A5A6"))
    }

    # 5. Seasonality
    pts <- tagList(pts, point(
      "5. Timing / cash-flow consideration",
      HTML(seas_txt), "#2C3E50"))

    # 6. Method agreement
    if (!is.null(r$blend)) {
      tot <- r$blend$totals
      spread <- max(tot$Total.IBNR) - min(tot$Total.IBNR)
      rel <- if (rec_total != 0) spread / abs(rec_total) else NA_real_
      agree_txt <- if (is.na(rel)) "" else
        if (rel < 0.10) "The methods broadly agree, which raises confidence in the booked figure."
        else if (rel < 0.25) "The methods show moderate spread; the choice of method has a real but manageable effect."
        else "The methods disagree materially - the final number is sensitive to method choice and warrants explicit sign-off."
      pts <- tagList(pts, point(
        "6. Method agreement",
        HTML(sprintf(
          "Across all methods tried, total IBNR ranges over about <b>%s</b> (roughly %s of the recommended figure). %s",
          money(spread), pct(rel), agree_txt)),
        if (is.na(rel)) "#95A5A6" else if (rel < 0.10) "#27AE60"
        else if (rel < 0.25) "#F39C12" else "#E74C3C"))
    }

    tagList(
      headline,
      h5("Key points for the decision", style = "margin-top:6px; color:#2C3E50;"),
      pts,
      div(style = "font-size:.8em; color:#95A5A6; margin-top:12px;",
          paste("Figures reflect the current filter and settings. Adjust the prior,",
                "credibility steepness, and seasonality toggle in the sidebar to test",
                "sensitivity. This summary is decision support, not a substitute for",
                "actuarial sign-off."))
    )
  })

  # ---- 5. Download ----------------------------------------------------------
  output$download_results <- downloadHandler(
    filename = function()
      paste0("Reserving_Results_",
             gsub("[^A-Za-z0-9_-]+", "_", input$subcat_filter), "_",
             Sys.Date(), ".xlsx"),
    content = function(file) {
      r <- analysis_results(); req(r)
      sheets <- list(
        "Cumulative Triangle"  = r$cum_df,
        "Incremental Triangle" = r$incr_df,
        "Dev Factors"          = r$dev_df,
        "Reserve Estimates"    = r$reserve_df,
        "Method Comparison"    = r$method_cmp
      )
      if (!is.null(r$boot)) {
        sheets[["Bootstrap Quantiles"]] <- r$boot$quantiles
        sheets[["Bootstrap by Origin"]] <- r$boot$by_origin
      }
      if (!is.null(r$bf))     sheets[["Bornhuetter-Ferguson"]] <- r$bf
      if (!is.null(r$blend)) {
        sheets[["Blended by Origin"]]   <- r$blend$detail
        sheets[["Blended Method Totals"]] <- r$blend$totals
      }
      if (!is.null(r$recast) && is.null(r$recast$error) &&
          !is.null(r$recast$detail)) {
        sheets[["Recast Summary"]] <- r$recast$summary
        sheets[["Recast Detail"]]  <- r$recast$detail
      }
      # Actuary adjustments / booked position
      ar <- tryCatch(adjusted_reserves(), error = function(e) NULL)
      if (!is.null(ar)) {
        sheets[["Final Selection"]] <- ar %>%
          select(Origin, Latest, Pct.Dev, Basis, Base.Ultimate, AdjPct,
                 AdjAmt, Override, Selected.Ultimate, Selected.IBNR,
                 Judgement, Comment)
        log_df <- ar %>% filter(abs(Judgement) > 1e-9 | !is.na(Edited)) %>%
          select(Origin, Basis, AdjPct, AdjAmt, Override, Judgement,
                 Comment, LastEdited = Edited)
        if (nrow(log_df)) sheets[["Adjustment Log"]] <- log_df
        sheets[["Sign-off"]] <- tibble(
          Field = c("Reviewing actuary", "Role / credential",
                    "Overall rationale", "Model total IBNR",
                    "Booked total IBNR", "Judgemental overlay",
                    "Export date"),
          Value = c(as.character(input$signoff_name %||% ""),
                    as.character(input$signoff_role %||% ""),
                    as.character(input$signoff_notes %||% ""),
                    format(round(sum(ar$Model.IBNR)), big.mark = ","),
                    format(round(sum(ar$Selected.IBNR)), big.mark = ","),
                    format(round(sum(ar$Judgement)), big.mark = ","),
                    as.character(Sys.Date())))
      }
      if (!is.null(r$seas))   sheets[["Seasonality Index"]] <- r$seas$index
      if (!is.null(r$exp_al)) {
        sheets[["Exposure Aligned"]] <- r$exp_al$aligned
        if (!is.null(r$blend) && isTRUE(r$blend$has_exposure)) {
          sheets[["A-priori Loss Ratios"]] <- tibble(
            Basis = c("Earned premium (BF / Benktander)", "Exposure (Cape Cod)"),
            LossRatio = c(r$blend$elr_premium, r$blend$elr_capecod))
        }
      }
      write_xlsx(sheets, path = file)
      showNotification("Results downloaded.", type = "message", duration = 4)
    }
  )
}

shinyApp(ui = ui, server = server)
