#!/usr/bin/env python3
"""Agent 3: EOD equity delivery strength from NSE Equity UDiFF and Security Delivery."""
import argparse, json, re, sys
from pathlib import Path

def nk(k): return re.sub(r"[^A-Z0-9]", "", str(k).upper())
def num(v):
    try:
        s=str(v).strip().replace(",","").replace("%","")
        return None if s in {"","-","--","NA","N/A","NULL"} else float(s)
    except (TypeError,ValueError): return None
def flatten(files): return [r for f in files for r in f.get("records",[])]
def keyed(rows, field):
    out={}
    for raw in rows:
        r={nk(k):v for k,v in raw.items()}
        s=str(r.get(field,"")).strip().upper()
        if s and s not in {"TOTAL","NAN","NONE"}: out[s]=r
    return out
def build_rows(payload):
    eq=keyed(flatten(payload["reports"]["equity_udiff"]),"TCKRSYMB")
    dl=keyed(flatten(payload["reports"]["security_delivery"]),"SYMBOL")
    out=[]
    for symbol,r in dl.items():
        e=eq.get(symbol,{})
        # The delivery report also includes non-equity instruments (for example GS series).
        # Stock ranking is restricted to the EQ series to avoid mixing unlike instruments.
        if str(r.get("SERIES","")).strip().upper() != "EQ": continue
        qty,pct=num(r.get("DELIVQTY")),num(r.get("DELIVPER"))
        if qty is None or pct is None or qty<0 or not 0<=pct<=100: continue
        close=num(r.get("CLOSEPRICE") or e.get("CLSPRIC"))
        prev=num(r.get("PREVCLOSE") or e.get("PRVSCLSGPRIC"))
        out.append({"symbol":symbol,"delivery_quantity":qty,"delivery_percentage":pct,
          "traded_quantity":num(r.get("TTLTRDQNTY") or e.get("TTLTRADGVOL")),
          "close_price":close,"previous_close":prev,
          "price_change_percentage":((close-prev)/prev*100 if close is not None and prev not in (None,0) else None),
          "equity_bhavcopy_match":bool(e)})
    if not out: raise ValueError("No valid security delivery rows found")
    return out
def previous_snapshot(root, day):
    if not root: return None
    candidates=[]
    for p in root.glob("*/delivery_strength.json"):
        if p.parent.name>=day: continue
        try:
            x=json.loads(p.read_text())
            if x.get("agent")=="equity_delivery_strength" and x.get("status")=="passed":
                # Do not compare widely separated pilot dates as if they were
                # the previous trading session.
                from datetime import date
                gap=(date.fromisoformat(day)-date.fromisoformat(p.parent.name)).days
                if 0 < gap <= 7: candidates.append((p.parent.name,p))
        except (OSError,json.JSONDecodeError): pass
    return sorted(candidates)[-1][1] if candidates else None
def run(source, output, history):
    p=json.loads(source.read_text())
    if p.get("status")!="passed" or p.get("date_consistency")!="passed": raise ValueError("Normalization validation did not pass")
    for k in ("equity_udiff","security_delivery"):
        if not p.get("reports",{}).get(k): raise ValueError("Required report missing: "+k)
    rows=build_rows(p)
    med_pct=sorted(x["delivery_percentage"] for x in rows)[len(rows)//2]
    med_qty=sorted(x["delivery_quantity"] for x in rows)[len(rows)//2]
    for x in rows:
        x["delivery_percentage_vs_median"]=round(x["delivery_percentage"]-med_pct,4)
        x["delivery_quantity_vs_median"]=round(x["delivery_quantity"]-med_qty,4)
    prev_path=previous_snapshot(history,p["requested_date"])
    old=json.loads(prev_path.read_text()) if prev_path else None
    oldmap={x["symbol"]:x for x in old.get("stocks",[])} if old else {}
    changes={}
    for x in rows:
        y=oldmap.get(x["symbol"])
        if y: changes[x["symbol"]]={"delivery_percentage_change_pp":round(x["delivery_percentage"]-y["delivery_percentage"],4),"delivery_quantity_change":x["delivery_quantity"]-y["delivery_quantity"]}
    result={"schema_version":1,"agent":"equity_delivery_strength","status":"passed","data_scope":"EOD equity delivery only",
      "trading_date":p["requested_date"],"comparison_date":prev_path.parent.name if prev_path else None,
      "universe_symbols":len(rows),"equity_bhavcopy_matches":sum(x["equity_bhavcopy_match"] for x in rows),
      "unmatched_delivery_symbols":[x["symbol"] for x in rows if not x["equity_bhavcopy_match"]],
      "universe_median_delivery_percentage":med_pct,"universe_median_delivery_quantity":med_qty,
      "top_delivery_percentage":sorted(rows,key=lambda x:x["delivery_percentage"],reverse=True)[:25],
      "top_delivery_quantity":sorted(rows,key=lambda x:x["delivery_quantity"],reverse=True)[:25],
      "stocks":rows,"changes_vs_previous_day":changes if old else None,
      "limitations":["High delivery percentage alone does not prove accumulation or institutional buying.","EOD delivery data does not identify buyer or seller identity.","Missing values are not estimated; unmatched symbols are disclosed.","No intraday or prediction-accuracy claims are made."]}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    return result
def main():
    a=argparse.ArgumentParser(); a.add_argument("--input",required=True); a.add_argument("--output")
    a.add_argument("--history-root",default="data/analysis"); z=a.parse_args()
    src=Path(z.input); dst=Path(z.output or f"data/analysis/{src.parent.name}/delivery_strength.json")
    r=run(src,dst,Path(z.history_root))
    print(json.dumps({"status":r["status"],"trading_date":r["trading_date"],"comparison_date":r["comparison_date"],"symbols":r["universe_symbols"],"equity_bhavcopy_matches":r["equity_bhavcopy_matches"],"top_delivery_percentage":r["top_delivery_percentage"][:5],"output":str(dst)},indent=2))
if __name__=="__main__":
    try: main()
    except Exception as e: print("ERROR:",e,file=sys.stderr); sys.exit(1)
