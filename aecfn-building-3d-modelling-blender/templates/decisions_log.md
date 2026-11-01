# Decisions log — <project>

Precedence when sources disagree: source spec > markups > underlays > photos. The source spec is never edited.
`python3 -m pipeline.run decisions` checks that every id below exists. Keep one row per id; first column = id.

## Open issues (OI-n from the source spec `open_issues[]` and the markups' red clouds)
| id | decision | reason | date |
|---|---|---|---|
| OI-0 | … | … | 2026-01-01 |

## Assumptions (declared in each zone's `ASSUMPTIONS`; values the spec does not have)
| id | key | value | hung from | reason |
|---|---|---|---|---|
| A-STR-01 | assumed_slab_depth_m | 0.15 | SSL (down) | not on any sheet; typical composite deck |

## Conflicts between sources
| id | sources | values | adopted | reason |
|---|---|---|---|---|
| C-01 | 1999 plan vs 2024 CLEUD | r 28.10 vs 28.43 | 2024 | as-built era is the model target |
