# PMI Franchise Onboarding Dashboard

Internal dashboard tracking the Blanket × PMI partnership through three stages:
**In Pipeline → Started Onboarding → Signed Up & Activated.**

It merges two sources, deduped by office:
- **PMI Onboarding Funnel** (Google Sheet) — onboarding source of truth (funnel phase, activation, live status)
- **PMI Sales Pipeline** (HubSpot) — sales pipeline + signed deals

## Viewing it
Open `index.html` in any browser. This committed copy is **redacted** (no admin emails, no deal $) so it's safe to share internally.

## Bucketing logic (see `build.py`)
- **Activated** — `onboarding_phase = PLATFORM_SETUP_COMPLETED` **or** live in platform
- **Onboarding** — in the funnel but not yet activated (+ Closed-Won deals not yet in the funnel = "Signed – onboarding pending")
- **Pipeline** — open HubSpot deal, not yet onboarding

## Rebuilding locally
```
# full version (with deal $) for internal use
python3 build.py
# redacted version (what's committed/shared)
REDACT=1 python3 build.py
```
Requires `data/pmi_onboarding_funnel.csv` (sheet export) and `data/hubspot_pmi_sales.json` (HubSpot pull). These are git-ignored because they contain emails + revenue.

## Auto-refresh
A scheduled Claude routine re-exports the funnel sheet, re-pulls the HubSpot PMI Sales Pipeline, rebuilds the redacted dashboard, and pushes daily.
