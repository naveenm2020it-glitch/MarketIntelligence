#!/usr/bin/env python3
"""Agent 5: NIFTY EOD option OI support/resistance and max-pain analysis."""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path

NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
OPTION_TYPES = {"IDO", "STO", "OPTIDX", "OPTSTK"}

def nk(v): return re.sub(r"[^A-Z0-9]", "", str(v).upper())
def num(v):
    if v is None: return None
    s = str(v).strip().replace(",", "")
    if s in {"", "-", "--", "NA", "N/A", "NULL"} or not NUMERIC.match(s): return None
    return float(s)
def first(row, *keys):
    for k in keys:
        if k in row:
            v = num(row[k])
            if v is not None: return v
    return None

def analyze(files):
    rows = [r for f in files for r in f.get("records", [])]
    if not rows: raise ValueError("F&O Bhavcopy contains no records")
    grouped = defaultdict(lambda: defaultdict(lambda: {"CE_OI":0.0,"PE_OI":0.0,"CE_CHANGE_OI":0.0,"PE_CHANGE_OI":0.0}))
    skipped = defaultdict(int); invalid = 0
    for raw in rows:
        r = {nk(k):v for k,v in raw.items()}
        inst = str(r.get("FININSTRMTP") or r.get("FININSTRMTYPE") or r.get("INSTRUMENT") or "").strip().upper()
        if inst not in OPTION_TYPES:
            skipped[inst or "missing_instrument"] += 1; continue
        symbol = str(r.get("TCKRSYMB") or r.get("SYMBOL") or "").strip().upper()
        if symbol != "NIFTY":
            skipped["non_nifty_option"] += 1; continue
        opt = str(r.get("OPTNTP") or r.get("OPTIONTYPE") or r.get("OPTIONTYP") or "").strip().upper()
        opt = {"CALL":"CE","PUT":"PE"}.get(opt,opt)
        strike = first(r,"STRKPRIC","STRIKEPR","STRIKEPRICE","STRIKE")
        oi = first(r,"OPNINTRST","OPENINTEREST")
        chg = first(r,"CHNGINOPNINTRST","CHANGEINOPENINTEREST","CHNGINOPNINT")
        expiry = str(r.get("XPRYDT") or r.get("EXPIRYDT") or "").strip()
        if opt not in {"CE","PE"} or strike is None or oi is None or chg is None or not expiry:
            invalid += 1; continue
        grouped[expiry][strike][opt+"_OI"] += oi
        grouped[expiry][strike][opt+"_CHANGE_OI"] += chg
    if not grouped: raise ValueError("No valid NIFTY option rows with expiry, strike, CE/PE, OI and change in OI")
    results=[]
    for expiry, sm in sorted(grouped.items()):
        strikes=sorted(sm)
        ce={s:sm[s]["CE_OI"] for s in strikes}; pe={s:sm[s]["PE_OI"] for s in strikes}
        cc={s:sm[s]["CE_CHANGE_OI"] for s in strikes}; pc={s:sm[s]["PE_CHANGE_OI"] for s in strikes}
        pain={}
        for settle in strikes:
            pain[settle]=sum(max(settle-k,0)*v for k,v in ce.items())+sum(max(k-settle,0)*v for k,v in pe.items())
        minpain=min(pain.values()); ties=[s for s,v in pain.items() if v==minpain]
        sr=sorted(strikes,key=lambda s:(pe[s],pc[s]),reverse=True)
        rr=sorted(strikes,key=lambda s:(ce[s],cc[s]),reverse=True)
        results.append({"expiry":expiry,"strike_count":len(strikes),
          "strikes":[{"strike":s,"call_oi":ce[s],"put_oi":pe[s],"call_change_oi":cc[s],"put_change_oi":pc[s],"net_put_minus_call_oi":pe[s]-ce[s]} for s in strikes],
          "support_levels":[{"strike":s,"put_oi":pe[s],"put_change_oi":pc[s],"rank":i+1} for i,s in enumerate(sr[:5])],
          "resistance_levels":[{"strike":s,"call_oi":ce[s],"call_change_oi":cc[s],"rank":i+1} for i,s in enumerate(rr[:5])],
          "max_pain":ties[0] if len(ties)==1 else None,"max_pain_tied_strikes":ties,"max_pain_writer_payout_oi_points":minpain})
    return {"expiries":results,"valid_nifty_option_strikes":sum(x["strike_count"] for x in results),
            "valid_expiries":len(results),"invalid_nifty_option_rows":invalid,"skipped_rows":dict(sorted(skipped.items()))}

def run(input_path, output_path):
    p=json.loads(input_path.read_text(encoding="utf-8"))
    if p.get("status")!="passed" or p.get("date_consistency")!="passed": raise ValueError("Normalization status/date consistency must both be passed")
    files=p.get("reports",{}).get("fo_udiff",[])
    if not files: raise ValueError("Required F&O UDiFF report is missing")
    result={"schema_version":1,"agent":"nifty_option_oi_intelligence","status":"passed","data_scope":"NIFTY options EOD OI by expiry and strike","trading_date":p.get("requested_date"),**analyze(files),
      "method":"Support ranks by Put OI then Put change in OI; resistance ranks by Call OI then Call change in OI. Max Pain minimizes aggregate intrinsic-value payout over observed strikes using reported OI.",
      "limitations":["EOD snapshot only; not live option-chain movement or intraday order flow.","OI concentrations are zones, not guaranteed support/resistance.","Max Pain is descriptive, not a price forecast.","Only observed strikes are considered; missing strikes are not interpolated.","No prediction accuracy is claimed without historical walk-forward validation."]}
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output")
    a=ap.parse_args(); src=Path(a.input); out=Path(a.output or f"data/analysis/{src.parent.name}/nifty_option_oi_intelligence.json")
    r=run(src,out); print(json.dumps({"status":r["status"],"trading_date":r["trading_date"],"valid_expiries":r["valid_expiries"],"valid_nifty_option_strikes":r["valid_nifty_option_strikes"],"output":str(out)},indent=2))
if __name__=="__main__":
    try: main()
    except Exception as e: print(f"ERROR: {e}",file=sys.stderr); raise SystemExit(1)
