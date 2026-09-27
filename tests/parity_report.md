# R vs Python parity report

| Scenario | Sheets | Cells compared | Numeric cells | Max relative diff | Result |
|---|---:|---:|---:|---:|---|
| m_A | 14 | 3,997 | 2,507 | 7.3e-12 | match |
| m_B_exposure_holdback6 | 18 | 5,686 | 3,695 | 2.0e-12 | match |
| m_C | 14 | 3,357 | 2,200 | 6.0e-12 | match |
| m_all_default | 16 | 4,824 | 2,925 | 1.5e-12 | match |
| m_all_expo_month_names | 18 | 4,936 | 3,071 | 1.5e-12 | match |
| m_all_exposure_capecod_tail_odp | 18 | 5,290 | 3,367 | 5.8e-11 | match |
| q_A | 14 | 1,061 | 699 | 1.1e-12 | match |
| q_B_exposure_tail | 18 | 1,216 | 810 | 3.1e-14 | match |
| q_C_bf | 14 | 949 | 622 | 1.1e-12 | match |
| q_all_bulk_basis | 16 | 1,216 | 774 | 7.2e-13 | match |
| q_all_default | 16 | 1,176 | 759 | 7.2e-13 | match |
| q_all_expo_no_match | 16 | 1,176 | 759 | 7.2e-13 | match |
| q_all_expo_partial | 18 | 1,216 | 793 | 7.2e-13 | match |
| q_all_expo_quarter_strings | 18 | 1,216 | 809 | 7.2e-13 | match |
| q_all_expo_serials | 18 | 1,216 | 809 | 7.2e-13 | match |
| q_all_exposure_bf | 18 | 1,258 | 844 | 7.2e-13 | match |
| q_all_holdback6 | 16 | 1,206 | 784 | 7.2e-13 | match |
| q_all_tail | 16 | 1,176 | 760 | 1.6e-13 | match |
| m_A_asis (R without fix) | 14 | 1,611 | 1,170 | 1.0e+00 | R bug: total IBNR 198,564,914 vs 198,570,529 when fixed |
| m_C_asis (R without fix) | 14 | 1,611 | 1,162 | 1.0e+00 | R bug: total IBNR 25,534,997 vs 25,536,718 when fixed |
