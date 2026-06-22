#!/usr/bin/env python3
"""
PMI Franchise Onboarding Dashboard generator.

Merges two sources into one shareable HTML dashboard:
  1. PMI Onboarding Funnel  (Google Sheet -> data/sheet_active.json, or a live published CSV)
  2. PMI Sales Pipeline     (HubSpot      -> data/hubspot_pmi_sales.json)

Three high-level buckets (Ben's view):
  - In Pipeline           : open HubSpot deals, not yet in onboarding
  - Started Onboarding     : in the funnel, account not yet activated (+ signed deals awaiting onboarding)
  - Signed Up & Activated  : admin activated their account OR office is live in platform

Run:  python3 build.py            -> writes index.html
Env:  SHEET_CSV_URL=<published csv>  uses the live sheet instead of the json snapshot
"""
import json, os, csv, io, datetime, html, urllib.request, re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ---- brand ----
PLUM = "#362642"; PINK = "#F24476"; LAV = "#EDE7F0"; INK = "#2B2230"; MUTE = "#8A7F92"

# HubSpot PMI Sales Pipeline stage map
STAGES = {
    "1353206030": "Opportunity", "1353206031": "Demo Completed",
    "1353206032": "Negotiations Started", "1353206033": "Agreement Sent",
    "1353206034": "Closed Won", "1353206035": "Closed Lost",
    "1353206036": "Churned", "1353206037": "On Hold",
}
OPEN_STAGES = {"Opportunity", "Demo Completed", "Negotiations Started", "Agreement Sent", "On Hold"}
DEAD_STAGES = {"Closed Lost", "Churned"}

PHASE_LABEL = {
    "FORM_SUBMITTED": "Form submitted", "OFFICE_SELECTED": "Office selected",
    "ONBOARDING_STARTED": "Onboarding started", "PRODUCTS_SELECTED": "Products selected",
    "PLATFORM_SETUP_COMPLETED": "Platform setup complete",
}

# --- office name matching (space / suffix / plural proof) ---
GENERIC = {"pmi", "deal", "deals", "llc", "inc", "the", "and", "co",
           "realty", "property", "properties", "management", "mgmt",
           "group", "services", "service", "pa"}

def _singular(t):
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t

def tokens(name):
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    raw = [t for t in s.split() if t and t != "pmi"]
    sig = [_singular(t) for t in raw if t not in GENERIC]
    if not sig:                      # name was all-generic; fall back
        sig = [_singular(t) for t in raw]
    return sig

def same_office(ta, tb):
    """True if two token lists denote the same office."""
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb):   # despaced equal:  massbay == mass+bay
        return True
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return True
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(small) >= 2 and small <= big   # subset, but only on 2+ real tokens

# ---------- load sheet ----------
def _tb(v):
    return str(v).strip().upper() in ("TRUE", "1", "YES")

def _parse_sheet_rows(rows):
    """Master-tab rows -> engaged offices (in the onboarding funnel)."""
    out = []
    for r in rows:
        # onboarding_phase is the reliable funnel stage; effective_phase is an
        # inconsistent derived column (mislabeled for advanced offices), so it's
        # only a fallback for early offices where onboarding_phase is blank.
        ph_onb = (r.get("onboarding_phase") or "").strip().upper()
        ph_eff = (r.get("effective_phase") or "").strip().upper()
        ph = ph_onb or ph_eff
        live = _tb(r.get("live_in_platform"))
        act = _tb(r.get("admin_activated"))
        status = (r.get("clickup_status") or "").strip().lower()
        # "engaged" = actually in the funnel, not just a directory entry
        if not (ph or live or act or status in ("live", "churned")):
            continue
        out.append({"company": (r.get("company_name") or "").strip(),
                    "effective_phase": ph,
                    "admin_activated": act, "live": live,
                    "clickup_status": status,
                    "admin_email": (r.get("admin_email") or "").strip(),
                    "days_in_onboarding": r.get("days_in_onboarding") or None})
    return out

def load_sheet():
    # 1) live published CSV (auto-refresh)
    url = os.environ.get("SHEET_CSV_URL", "").strip()
    if url:
        with urllib.request.urlopen(url, timeout=60) as r:
            rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
        return _parse_sheet_rows(rows), "live sheet (published CSV)"
    # 2) full exported master CSV (current snapshot)
    csv_path = os.path.join(DATA, "pmi_onboarding_funnel.csv")
    if os.path.exists(csv_path):
        with open(csv_path, newline="") as f:
            return _parse_sheet_rows(list(csv.DictReader(f))), "sheet export (full master CSV)"
    # 3) legacy hand-built fallback
    j = json.load(open(os.path.join(DATA, "sheet_active.json")))
    return j["offices"], "sheet snapshot (data/sheet_active.json)"

# ---------- load hubspot ----------
def load_hubspot():
    j = json.load(open(os.path.join(DATA, "hubspot_pmi_sales.json")))
    out = []
    for d in j.get("results", []):
        p = d["properties"]
        name = re.sub(r"\s+(PA\s+)?Deal$", "", (p.get("dealname") or "").strip())
        out.append({"company": name,
                    "stage": STAGES.get(p.get("dealstage", ""), p.get("dealstage", "")),
                    "amount": float(p["amount"]) if p.get("amount") else None,
                    "closedate": (p.get("closedate") or "")[:10]})
    return out

def fmt_money(v):
    return "${:,.0f}".format(v) if v else "—"

# ---------- merge & bucket ----------
def build():
    sheet, sheet_src = load_sheet()
    deals = load_hubspot()

    recs = []
    index = []   # list of (token_list, record) for dedupe matching

    # 1) seed from sheet (onboarding truth)
    # Bucket follows the FUNNEL PHASE: platform-setup-complete (or live) = activated;
    # office-selected / onboarding-started / products-selected / form-submitted = onboarding.
    for o in sheet:
        setup_complete = o["effective_phase"] == "PLATFORM_SETUP_COMPLETED"
        activated = setup_complete or bool(o["live"])
        churned = (o.get("clickup_status") or "").lower() == "churned"
        bucket = "churned" if churned else ("activated" if activated else "onboarding")
        rec = {
            "company": o["company"], "bucket": bucket,
            "phase": PHASE_LABEL.get(o["effective_phase"], o["effective_phase"].title()),
            "setup_complete": setup_complete,
            "live": o["live"], "admin_activated": o["admin_activated"],
            "email": o.get("admin_email", ""), "days": o.get("days_in_onboarding"),
            "deal_stage": "", "amount": None, "in_sheet": True, "in_hs": False,
        }
        recs.append(rec)
        index.append((tokens(o["company"]), rec))

    # 2) layer in HubSpot deals — merge onto a matching office, else add new
    for d in deals:
        dt = tokens(d["company"])
        match = next((rec for tks, rec in index if same_office(dt, tks)), None)
        if match:
            match["in_hs"] = True
            match["deal_stage"] = d["stage"]
            if match["amount"] is None:
                match["amount"] = d["amount"]
            continue
        if d["stage"] in DEAD_STAGES:
            bucket = "churned"
        elif d["stage"] == "Closed Won":
            bucket = "onboarding"   # signed, onboarding not yet started in funnel
        else:
            bucket = "pipeline"
        rec = {
            "company": d["company"], "bucket": bucket,
            "phase": "Signed – onboarding pending" if d["stage"] == "Closed Won" else "—",
            "setup_complete": False,
            "live": False, "admin_activated": False, "email": "", "days": None,
            "deal_stage": d["stage"], "amount": d["amount"], "in_sheet": False, "in_hs": True,
        }
        recs.append(rec)
        index.append((dt, rec))

    counts = {b: sum(1 for r in recs if r["bucket"] == b) for b in ("pipeline", "onboarding", "activated", "churned")}
    pipe_val = sum(r["amount"] or 0 for r in recs if r["bucket"] == "pipeline")
    return recs, counts, pipe_val, sheet_src

# ---------- render ----------
def render(recs, counts, pipe_val, sheet_src):
    redact = os.environ.get("REDACT", "").strip().lower() in ("1", "true", "yes")
    today = datetime.date.today().strftime("%B %-d, %Y")
    order = {"activated": 0, "onboarding": 1, "pipeline": 2, "churned": 3}
    recs.sort(key=lambda r: (order[r["bucket"]], -(r["amount"] or 0), r["company"].lower()))

    total_active = counts["pipeline"] + counts["onboarding"] + counts["activated"]
    funnel = [("In Pipeline", counts["pipeline"], "#B9A7C7"),
              ("Started Onboarding", counts["onboarding"], PINK),
              ("Signed Up & Activated", counts["activated"], PLUM)]
    fmax = max(c for _, c, _ in funnel) or 1

    def badge(r):
        b = r["bucket"]
        col = {"activated": PLUM, "onboarding": PINK, "pipeline": "#9B89AB", "churned": "#C0392B"}[b]
        lab = {"activated": "Activated", "onboarding": "Onboarding", "pipeline": "Pipeline", "churned": "Churned"}[b]
        return f'<span class="pill" style="background:{col}">{lab}</span>'

    def src_tags(r):
        t = ""
        if r["in_sheet"]: t += '<span class="src s-sheet">Funnel</span>'
        if r["in_hs"]:    t += '<span class="src s-hs">HubSpot</span>'
        return t or '<span class="src">—</span>'

    rows = ""
    for r in recs:
        if r["live"]:
            live = '<span class="dot live"></span>Live'
        elif r.get("setup_complete"):
            live = '<span class="dot setup"></span>Setup complete'
        elif r["admin_activated"]:
            live = '<span class="dot act"></span>Activated'
        else:
            live = '<span class="dot"></span>—'
        days = f'{r["days"]}d' if r.get("days") not in (None, "", "null") else "—"
        deal = html.escape(r["deal_stage"]) if r["deal_stage"] else "—"
        amt_td = "" if redact else f'<td class="num">{fmt_money(r["amount"])}</td>'
        rows += f"""<tr data-bucket="{r['bucket']}">
          <td class="co">{html.escape(r['company'])}</td>
          <td>{badge(r)}</td>
          <td>{html.escape(r['phase'])}</td>
          <td>{live}</td>
          <td>{deal}</td>
          {amt_td}
          <td class="days">{days}</td>
          <td>{src_tags(r)}</td>
        </tr>"""

    funnel_html = ""
    for lab, c, col in funnel:
        w = max(6, int(c / fmax * 100))
        funnel_html += f"""<div class="frow">
          <div class="flab">{lab}</div>
          <div class="ftrack"><div class="fbar" style="width:{w}%;background:{col}">{c}</div></div>
        </div>"""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PMI Franchise Onboarding Dashboard</title>
<style>
  :root{{--plum:{PLUM};--pink:{PINK};--lav:{LAV};--ink:{INK};--mute:{MUTE};}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif;color:var(--ink);background:#FAF8FB}}
  .wrap{{max-width:1140px;margin:0 auto;padding:32px 24px 64px}}
  header{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;border-bottom:3px solid var(--plum);padding-bottom:18px}}
  h1{{margin:0;font-size:26px;color:var(--plum);letter-spacing:-.4px}}
  .sub{{color:var(--mute);font-size:13px;margin-top:4px}}
  .brand{{font-weight:700;color:var(--pink)}}
  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:26px 0}}
  .card{{background:#fff;border:1px solid #ECE4F0;border-radius:14px;padding:18px 18px 16px;box-shadow:0 1px 3px rgba(54,38,66,.05)}}
  .card .n{{font-size:34px;font-weight:750;line-height:1;color:var(--plum)}}
  .card .l{{font-size:12.5px;color:var(--mute);margin-top:8px;text-transform:uppercase;letter-spacing:.5px}}
  .card.pink .n{{color:var(--pink)}}
  .card .sub2{{font-size:12px;color:var(--mute);margin-top:6px}}
  .panel{{background:#fff;border:1px solid #ECE4F0;border-radius:14px;padding:22px 24px;margin-bottom:26px;box-shadow:0 1px 3px rgba(54,38,66,.05)}}
  .panel h2{{margin:0 0 16px;font-size:15px;color:var(--plum);text-transform:uppercase;letter-spacing:.6px}}
  .frow{{display:flex;align-items:center;gap:14px;margin:10px 0}}
  .flab{{width:190px;font-size:13.5px;font-weight:600;text-align:right;color:var(--ink)}}
  .ftrack{{flex:1;background:var(--lav);border-radius:8px;overflow:hidden}}
  .fbar{{color:#fff;font-weight:700;font-size:13px;padding:9px 12px;border-radius:8px;text-align:right;min-width:34px;transition:width .4s}}
  .toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}
  .fbtn{{border:1px solid #DcccE4;background:#fff;color:var(--plum);border-radius:999px;padding:7px 14px;font-size:13px;cursor:pointer;font-weight:600}}
  .fbtn.on{{background:var(--plum);color:#fff;border-color:var(--plum)}}
  input#q{{margin-left:auto;border:1px solid #DcccE4;border-radius:999px;padding:7px 14px;font-size:13px;min-width:200px}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px}}
  th{{text-align:left;color:var(--mute);font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;padding:8px 10px;border-bottom:2px solid #Eee}}
  td{{padding:10px;border-bottom:1px solid #F1ECF4;vertical-align:middle}}
  td.co{{font-weight:600;color:var(--plum)}}
  td.num,td.days{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  tr:hover td{{background:#FBF7FD}}
  .pill{{color:#fff;font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap}}
  .src{{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:5px;margin-right:4px;background:#Eee;color:#777}}
  .s-sheet{{background:#E7F0FB;color:#2D6FB8}} .s-hs{{background:#FCE6EF;color:#C32B66}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#CFC6D6;margin-right:6px}}
  .dot.live{{background:#2EA86B}} .dot.act{{background:var(--pink)}} .dot.setup{{background:var(--plum)}}
  footer{{color:var(--mute);font-size:12px;text-align:center;margin-top:20px}}
</style></head><body><div class="wrap">
  <header>
    <div>
      <h1>PMI Franchise Onboarding</h1>
      <div class="sub"><span class="brand">Blanket</span> &times; PMI partnership &middot; pipeline &rarr; onboarding &rarr; activation</div>
    </div>
    <div class="sub">Updated {today}</div>
  </header>

  <div class="cards">
    <div class="card"><div class="n">{counts['pipeline']}</div><div class="l">In Pipeline</div><div class="sub2">{'open sales deals' if redact else fmt_money(pipe_val) + ' open deal value'}</div></div>
    <div class="card pink"><div class="n">{counts['onboarding']}</div><div class="l">Started Onboarding</div><div class="sub2">in setup, not yet live</div></div>
    <div class="card"><div class="n">{counts['activated']}</div><div class="l">Signed Up &amp; Activated</div><div class="sub2">account activated / live</div></div>
    <div class="card"><div class="n">{total_active}</div><div class="l">Total Engaged Offices</div><div class="sub2">{counts['churned']} churned / lost</div></div>
  </div>

  <div class="panel"><h2>Onboarding Funnel</h2>{funnel_html}</div>

  <div class="panel">
    <h2>Office Detail</h2>
    <div class="toolbar">
      <button class="fbtn on" data-f="all">All</button>
      <button class="fbtn" data-f="pipeline">Pipeline</button>
      <button class="fbtn" data-f="onboarding">Onboarding</button>
      <button class="fbtn" data-f="activated">Activated</button>
      <button class="fbtn" data-f="churned">Churned</button>
      <input id="q" placeholder="Search office…">
    </div>
    <table id="tbl"><thead><tr>
      <th>Office</th><th>Status</th><th>Funnel Phase</th><th>Platform</th>
      <th>HubSpot Stage</th>{'' if redact else '<th>Deal $</th>'}<th>Days</th><th>Source</th>
    </tr></thead><tbody>{rows}</tbody></table>
  </div>

  <footer>Sources: PMI Onboarding Funnel ({sheet_src}) + HubSpot PMI Sales Pipeline. Generated by Blanket GTM.</footer>
</div>
<script>
  const btns=document.querySelectorAll('.fbtn'), q=document.getElementById('q');
  let f='all';
  function apply(){{
    const term=q.value.toLowerCase();
    document.querySelectorAll('#tbl tbody tr').forEach(tr=>{{
      const okF = f==='all' || tr.dataset.bucket===f;
      const okQ = tr.querySelector('.co').textContent.toLowerCase().includes(term);
      tr.style.display = (okF&&okQ)?'':'none';
    }});
  }}
  btns.forEach(b=>b.onclick=()=>{{btns.forEach(x=>x.classList.remove('on'));b.classList.add('on');f=b.dataset.f;apply();}});
  q.oninput=apply;
</script>
</body></html>"""

if __name__ == "__main__":
    recs, counts, pipe_val, sheet_src = build()
    htmlout = render(recs, counts, pipe_val, sheet_src)
    out = os.path.join(HERE, "index.html")
    open(out, "w").write(htmlout)
    print(f"Wrote {out}")
    print(f"  Pipeline={counts['pipeline']}  Onboarding={counts['onboarding']}  "
          f"Activated={counts['activated']}  Churned={counts['churned']}  ({sheet_src})")
