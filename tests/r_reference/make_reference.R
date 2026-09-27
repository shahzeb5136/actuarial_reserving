# ============================================================================
#  Reference outputs from the ORIGINAL R app, for the Python parity tests.
# ----------------------------------------------------------------------------
#  Drives the real Shiny server in archive/r_shiny_app/app.R with shiny::testServer (no
#  browser): uploads the claims / exposure files, sets the sidebar inputs,
#  presses "Run analysis", edits the adjustments worksheet, fills in the
#  sign-off and downloads the Excel results.  For every scenario it saves
#     tests/reference/<id>.xlsx   the app's own Excel download
#     tests/reference/<id>.json   settings, edits applied, notifications,
#                                 rendered summary HTML, value boxes, Mack
#                                 internals and the bootstrap simulations
#  plus bootstrap internals / large-sample distributions and period-parsing
#  results.  Run from anywhere:   Rscript tests/r_reference/make_reference.R
# ============================================================================

# NB: no library() calls before sourcing app.R - attaching e.g. jsonlite first
# would mask shiny::validate() inside the app's server.  app.R attaches
# everything this script needs; jsonlite is called with `::`.

script_dir <- local({
  a <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  if (length(a)) dirname(normalizePath(sub("^--file=", "", a[1]))) else getwd()
})
tests_dir <- normalizePath(file.path(script_dir, ".."))
proj_dir  <- normalizePath(file.path(tests_dir, "..", "archive", "r_shiny_app"))
ref_dir   <- file.path(tests_dir, "reference")
fx_dir    <- file.path(tests_dir, "fixtures")
dir.create(ref_dir, showWarnings = FALSE); dir.create(fx_dir, showWarnings = FALSE)

setwd(proj_dir)
suppressPackageStartupMessages(source("app.R", local = globalenv()))
claims_path   <- normalizePath("aggregated_claims.xlsx")
exposure_path <- normalizePath("exposure_premium.xlsx")

write_ref <- function(x, name)
  jsonlite::write_json(x, file.path(ref_dir, name), digits = I(17), auto_unbox = TRUE,
                       na = "null", null = "null", pretty = FALSE)

# ---- Capture notifications instead of sending them to a browser -----------
.notes <- new.env(); .notes$items <- list()
showNotification <- function(ui, ..., type = "default") {
  .notes$items[[length(.notes$items) + 1]] <-
    list(type = type, text = paste(as.character(ui), collapse = ""))
  invisible(NULL)
}

# ---- Optional fix for the empty-lag-column bug ----------------------------
original_long_to_incr_matrix <- long_to_incr_matrix
patched_long_to_incr_matrix <- function(long_df) {
  long_df <- tidyr::complete(long_df, OriginPeriod,
                             DevLag = seq_len(max(long_df$DevLag)))
  original_long_to_incr_matrix(long_df)
}

# ---- Exposure fixtures in different layouts --------------------------------
local({
  ex <- readxl::read_excel(exposure_path)
  ex$Period <- as.Date(ex$Period)
  q <- ex %>% mutate(Q = floor_date(Period, "quarter")) %>%
    group_by(Q) %>% summarise(EarnedPremium = sum(EarnedPremium),
                              Exposure = sum(Exposure), .groups = "drop")
  write_xlsx(tibble(Period = paste0(year(q$Q), " Q", quarter(q$Q)),
                    EarnedPremium = q$EarnedPremium),
             file.path(fx_dir, "exposure_quarter_strings_premium_only.xlsx"))
  write_xlsx(tibble(Origin = format(ex$Period, "%b-%Y"),
                    Premium = ex$EarnedPremium, Units = ex$Exposure),
             file.path(fx_dir, "exposure_month_names_synonyms.xlsx"))
  write_xlsx(tibble(Period = as.numeric(ex$Period - as.Date("1899-12-30")),
                    EP = ex$EarnedPremium, Exposure = ex$Exposure),
             file.path(fx_dir, "exposure_excel_serials.xlsx"))
  write_xlsx(ex %>% filter(Period >= as.Date("2022-01-01")),
             file.path(fx_dir, "exposure_partial_2022_2023.xlsx"))
  write_xlsx(ex %>% mutate(Period = Period %m+% years(9)),
             file.path(fx_dir, "exposure_no_match.xlsx"))
})

# ---- Scenarios ------------------------------------------------------------
base <- list(subcat = "All Categories", period = "quarter", tail = 1, n_sims = 200,
             boot_dist = "gamma", n_holdback = 1, prior = "benktander",
             kappa = 1.5, seasonal = TRUE, exposure = NULL, patched = FALSE,
             bulk_basis = NULL)
sc <- function(id, ...) modifyList(base, c(list(id = id), list(...)))
scenarios <- list(
  sc("q_all_default"),
  sc("m_all_default", period = "month"),
  sc("q_all_exposure_bf", exposure = "exposure_premium.xlsx", prior = "bf",
     kappa = 2.0, n_holdback = 2),
  sc("m_all_exposure_capecod_tail_odp", period = "month", exposure = "exposure_premium.xlsx",
     tail = 1.05, prior = "capecod", kappa = 1.0, seasonal = FALSE, n_holdback = 3,
     boot_dist = "od.pois", n_sims = 100),
  sc("q_all_tail", tail = 1.1, kappa = 3.0),
  sc("q_all_holdback6", n_holdback = 6, prior = "capecod"),
  sc("q_all_bulk_basis", bulk_basis = "CL"),
  sc("q_A", subcat = "A", n_holdback = 2),
  sc("q_B_exposure_tail", subcat = "B", exposure = "exposure_premium.xlsx", tail = 1.02,
     prior = "bf", kappa = 1.2),
  sc("q_C_bf", subcat = "C", seasonal = FALSE, prior = "bf", kappa = 0.5),
  sc("m_A", subcat = "A", period = "month", patched = TRUE),
  sc("m_A_asis", subcat = "A", period = "month", patched = FALSE),
  sc("m_B_exposure_holdback6", subcat = "B", period = "month",
     exposure = "exposure_premium.xlsx", n_holdback = 6, prior = "capecod"),
  sc("m_C", subcat = "C", period = "month", patched = TRUE, seasonal = FALSE),
  sc("m_C_asis", subcat = "C", period = "month", patched = FALSE, seasonal = FALSE),
  sc("q_all_expo_quarter_strings", exposure = "fixtures/exposure_quarter_strings_premium_only.xlsx"),
  sc("m_all_expo_month_names", period = "month",
     exposure = "fixtures/exposure_month_names_synonyms.xlsx", prior = "bf"),
  sc("q_all_expo_serials", exposure = "fixtures/exposure_excel_serials.xlsx", prior = "capecod"),
  sc("q_all_expo_partial", exposure = "fixtures/exposure_partial_2022_2023.xlsx"),
  sc("q_all_expo_no_match", exposure = "fixtures/exposure_no_match.xlsx")
)

# Worksheet edits, by position from the newest origin.  Values are strings
# exactly as a user would type them into the DT table.
edit_plan <- function(mu) {
  n <- nrow(mu); row <- function(k) n - k
  list(
    list(row = row(0), col = "AdjPct",   value = "10"),
    list(row = row(1), col = "Override", value = format(round(mu$Latest[row(1)] * 1.25), scientific = FALSE)),
    list(row = row(2), col = "AdjAmt",   value = "-500,000"),
    list(row = row(3), col = "Basis",    value = "cl"),
    list(row = row(3), col = "Comment",  value = "Large claim pending"),
    list(row = row(4), col = "Override", value = "abc"),
    list(row = row(5), col = "AdjPct",   value = "150%"),
    list(row = row(6), col = "AdjAmt",   value = "xyz"),
    list(row = row(7), col = "Basis",    value = "Foo"),
    list(row = row(8), col = "Override", value = format(round(mu$Latest[row(8)] * 0.5), scientific = FALSE)),
    list(row = row(8), col = "Comment",  value = "Recoveries expected, per claims team")
  )
}
signoff <- list(name = "A. Actuary", role = "FIA",
                notes = "Parity test rationale, with commas, and 'quotes'.")

html_of <- function(x) if (is.list(x) && !is.null(x$html)) as.character(x$html) else as.character(x)

run_scenario <- function(s) {
  message("scenario ", s$id)
  long_to_incr_matrix <<- if (isTRUE(s$patched)) patched_long_to_incr_matrix
                          else original_long_to_incr_matrix
  .notes$items <- list()
  box <- new.env()
  testServer(server, {
    session$setInputs(data_file = data.frame(name = "aggregated_claims.xlsx", size = 1,
                      type = "", datapath = claims_path, stringsAsFactors = FALSE))
    if (!is.null(s$exposure))
      session$setInputs(exposure_file = data.frame(name = basename(s$exposure), size = 1,
                        type = "", datapath = normalizePath(file.path(
                          if (startsWith(s$exposure, "fixtures/")) tests_dir else proj_dir,
                          s$exposure)), stringsAsFactors = FALSE))
    session$setInputs(subcat_filter = s$subcat, period = s$period, tail = s$tail,
                      n_sims = s$n_sims, boot_dist = s$boot_dist, n_holdback = s$n_holdback,
                      blend_prior = s$prior, blend_kappa = s$kappa,
                      blend_seasonal = s$seasonal)
    session$setInputs(run_analysis = 1)
    r  <- analysis_results()
    mu <- method_ultimates()
    if (!is.null(s$bulk_basis)) {
      session$setInputs(adj_basis_all = s$bulk_basis)
      session$setInputs(adj_apply_basis = 1)
    }
    edits <- edit_plan(mu)
    for (e in edits)
      session$setInputs(adj_table_cell_edit = list(row = e$row, col = match(e$col, ADJ_COLS) - 1L,
                                                   value = e$value))
    session$setInputs(signoff_name = signoff$name, signoff_role = signoff$role,
                      signoff_notes = signoff$notes)
    ar <- adjusted_reserves()
    texts <- list(exec = html_of(output$exec_summary_ui),
                  final_headline = html_of(output$final_headline_ui),
                  guardrail = html_of(output$adj_guardrail),
                  exposure_status = html_of(output$exposure_status),
                  recast_status = html_of(output$recast_status))
    vb <- list(vb_rows = output$vb_rows, vb_total = output$vb_total, vb_span = output$vb_span,
               vb_adj_base = output$vb_adj_base, vb_adj_final = output$vb_adj_final,
               vb_adj_delta = output$vb_adj_delta, vb_exp_matched = output$vb_exp_matched,
               vb_exp_premium = output$vb_exp_premium, vb_exp_exposure = output$vb_exp_exposure,
               cum_caption = output$cum_caption)
    xlsx <- output$download_results
    file.copy(xlsx, file.path(ref_dir, paste0(s$id, ".xlsx")), overwrite = TRUE)
    m <- r$mack$model
    boot <- NULL
    if (!is.null(r$boot)) {
      ib <- r$boot$model$IBNR.ByOrigin
      boot <- list(ibnr_by_origin = matrix(ib, nrow = dim(ib)[1]),
                   process_distr = r$boot$model$process.distr)
    }
    box$out <- list(
      id = s$id, settings = s[setdiff(names(s), "id")], edits = edits, signoff = signoff,
      export_date = as.character(Sys.Date()),
      notifications = .notes$items, texts = texts, value_boxes = vb,
      mack = list(f = unname(m$f), f_se = unname(m$f.se), sigma = unname(m$sigma),
                  total_se = m$Total.Mack.S.E, full_triangle = unname(m$FullTriangle),
                  mack_se_last = unname(m$Mack.S.E[, ncol(m$Mack.S.E)])),
      boot = boot,
      recast_error = r$recast$error,
      heat = if (!is.null(r$seas)) as.data.frame(r$seas$heat %>% mutate(Yr = as.character(Yr),
                                                  Mnth = as.character(Mnth))) else NULL,
      adj_state = as.data.frame(adj_state() %>% mutate(Edited = !is.na(Edited))))
  })
  write_ref(box$out, paste0(s$id, ".json"))
}

for (s in scenarios) {
  set.seed(20260927)
  tryCatch(run_scenario(s), error = function(e) {
    message("  FAILED: ", conditionMessage(e))
    write_ref(list(id = s$id, settings = s, error = conditionMessage(e)), paste0(s$id, ".json"))
  })
}
long_to_incr_matrix <- original_long_to_incr_matrix

# ---- Bootstrap: deterministic internals + injected residual draws ---------
boot_internals <- function(cum, R, seed, distr) {
  ns <- asNamespace("ChainLadder")
  triangle <- ns$checkTriangle(cum)
  m <- dim(triangle)[1]; n <- dim(triangle)[2]; origins <- c((m - n + 1):m)
  tri3 <- array(triangle, dim = c(m, n, 1))
  inc <- ns$getIncremental(tri3)
  Latest <- ns$getDiagonal(tri3, m)
  avDFs <- ns$getAvDFs(ns$getIndivDFs(tri3), tri3)
  ultDFs <- ns$getUltDFs(avDFs)
  exp_inc <- ns$getIncremental(ns$getExpected(ns$getUltimates(Latest, ultDFs), 1 / ultDFs))
  exp_inc[is.na(inc[origins, , 1])] <- NA
  inc <- inc[origins, , ]; dim(inc) <- c(n, n, 1)
  unscaled <- (inc - exp_inc) / sqrt(abs(exp_inc))
  nobs <- 0.5 * n * (n + 1); sf <- nobs - 2 * n + 1
  phi <- sum(unscaled^2, na.rm = TRUE) / sf
  adj <- unscaled * sqrt(nobs / sf)
  samp <- ns$sampleResiduals(adj, exp_inc, R, seed)
  bcl <- BootChainLadder(cum, R = R, process.distr = distr, seed = seed)
  list(n = n, R = R, exp_inc = matrix(exp_inc, n), adj_resids = matrix(adj, n), phi = phi,
       resid_sample_colmajor = as.vector(samp),
       param_by_origin = matrix(bcl$ParamDist.ByOrigin, n),
       ibnr_by_origin = matrix(bcl$IBNR.ByOrigin, n))
}
claims <- readxl::read_excel(claims_path) %>%
  mutate(OriginDate = as.Date(OriginDate), PaymentDate = as.Date(PaymentDate))
tri_of <- function(period) incr_to_cum(long_to_incr_matrix(build_long_triangle(claims, period)))
cum_q <- tri_of("quarter"); cum_m <- tri_of("month")
message("bootstrap internals")
write_ref(list(quarter = boot_internals(cum_q, 200, 42, "gamma"),
               month = boot_internals(cum_m, 40, 7, "gamma")), "bootstrap_internals.json")

message("bootstrap large-sample distributions")
t_totals <- function(cum, R, distr) { set.seed(123); BootChainLadder(cum, R = R, process.distr = distr)$IBNR.Totals }
write_ref(list(
  quarter_gamma  = list(R = 20000, totals = t_totals(cum_q, 20000, "gamma")),
  quarter_odpois = list(R = 5000,  totals = t_totals(cum_q, 5000, "od.pois")),
  month_gamma    = list(R = 3000,  totals = t_totals(cum_m, 3000, "gamma"))),
  "bootstrap_distributions.json")

# ---- Exposure parsing -------------------------------------------------------
period_inputs <- c("2021-01-01", "2021-01-15", "2021-01", "2021/01", "2021/1/1", "2021/01/31",
                   "20210115", "202101", "Jan-2021", "Jan 2021", "January 2021", "january-2021",
                   "JAN 2021", "2021 Jan", "2021-Jan", "Jan2021", "Sep 2022", "2021 Q1", "2021-Q1",
                   "2021Q1", "2021 q3", "Q1 2021", "Q4-2022", "q2 2021", "Q12021", "44197",
                   "44197.75", "", "  2022-03-01  ", "2021.01")
parsed <- vapply(period_inputs, function(s) as.character(parse_period_cell(s)), character(1))
tables <- lapply(list.files(fx_dir, pattern = "\\.xlsx$", full.names = TRUE), function(p)
  list(file = basename(p), table = as.data.frame(read_exposure_table(p) %>%
       mutate(PeriodDate = as.character(PeriodDate)))))
write_ref(list(periods = data.frame(input = period_inputs, parsed = unname(parsed)),
               exposure_tables = tables), "exposure_parsing.json")

# ---- lowess (trend lines on the residual diagnostics) -----------------------
set.seed(99)
lx <- c(runif(60, 0, 10), rep(5, 5))            # includes ties
ly <- sin(lx) + rnorm(65, sd = 0.3)
ly[c(3, 17)] <- ly[c(3, 17)] + 4                 # outliers exercise the robustness weights
write_ref(list(x = lx, y = ly, fit = lowess(lx, ly),
               fit_f03_iter0 = lowess(lx, ly, f = 0.3, iter = 0)), "lowess.json")
message("done")
