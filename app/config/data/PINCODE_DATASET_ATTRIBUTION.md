# Attribution — PIN code → District/State dataset

This directory contains a derived extract of government open data, committed here per
`ARCHITECTURE.md` §5.4 ("the file is committed, not fetched at runtime").

## Attribution statement (per GODL-India)

Contains information derived from the "All India Pincode Directory till last month"
dataset, published by the Department of Posts, Ministry of Communications, Government of
India, on the Open Government Data (OGD) Platform India (data.gov.in). Sourced under the
Government Open Data License – India (GODL-India).

## Source

- **Dataset name:** All India Pincode Directory till last month
- **Publisher:** Department of Posts, Ministry of Communications, Government of India
- **Portal:** Open Government Data (OGD) Platform India — data.gov.in
- **Dataset page:** https://www.data.gov.in/resource/all-india-pincode-directory-till-last-month
- **API resource id:** `5c2f62fe-5afa-4119-a499-fec9d604d5bd`
- **License:** Government Open Data License – India (GODL-India), gazette-notified 2017-02-13
- **Dataset's own last-updated timestamp (per API metadata):** 2025-10-03T04:04:14Z
- **Retrieval date (this copy):** 2026-07-26
- **Original record count at retrieval:** 165,627 rows, 19,586 unique pincodes

## Derivation

`pincode_district_state.csv` in this directory is a minimal derivation of the source
dataset:

- Only three columns kept: `pincode`, `district`, `statename`. All other source columns
  (circle, region, division, office name, office type, delivery status, latitude,
  longitude) are dropped — not needed for PIN → District/State derivation.
- Exact-duplicate rows collapsed (the source lists one row per post office; many post
  offices share a pincode/district/state combination).
- Genuine ambiguity is **preserved, not resolved**: 1,478 of the 19,586 unique pincodes map
  to more than one distinct (district, state) combination in the source data (a known
  data-quality characteristic of this dataset — see `BUILD.md` Phase 3 risk table). Where
  that happens, multiple rows are kept for the same pincode. Resolving or flagging these
  at lookup time is Derive-function logic, implemented in Phase 3 — not addressed here.
- Result: 21,162 rows.

## License terms (summary)

GODL-India permits use, adaptation, publication (original or derivative), translation, and
redistribution for both commercial and non-commercial purposes, provided the source is
attributed as above. It does not imply endorsement by the data provider, and the provider
disclaims liability for errors, omissions, or continued availability of updates.
