#!/usr/bin/env python3
"""Agent 6: transparent EOD market-direction confluence from Agents 2-5."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any

AGENTS = {
    "participant": ("participant_oi_intelligence.json", "participant_oi_intelligence"),
    "delivery": ("delivery_strength.json", "equity_delivery_strength"),
    "futures": ("futures_buildup_intelligence.json", "futures_buildup_intelligence"),
    "options": ("nifty_option_oi_intelligence.json", "nifty_option_oi_intelligence"),
}

def read_snapshot(path: Path, expected_agent: str, trading_date: str) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {path.name}: {exc}") from exc
    if data.get("status") != "passed" or data.get("agent") != expected_agent:
        raise ValueError(f"{path.name} is not a passed {expected_agent} snapshot")
    if data.get("trading_date") != trading_date:
        raise ValueError(f"{path.name} trading date does not match {trading_date}")
    return data

def sign_label(value: float | None) -> str:
    if value is None: return "unavailable"
    if value > 0: return "bullish"
    if value < 0: return "bearish"
    return "neutral"

def participant_signal(data: dict | None) -> dict:
    if not data: return {"direction":"unavailable","available":False,"evidence":{}}
    parts=data.get("participants",{})
    evidence={}
    for name in ("FII","DII"):
        p=parts.get(name,{})
        vals=[p.get("index_futures_net_long"),p.get("index_options_directional_net")]
        known=[v for v in vals if isinstance(v,(int,float))]
        evidence[name]=sign_label(sum(known)/len(known) if known else None)
    known=[v for v in evidence.values() if v in ("bullish","bearish")]
    if not known: direction="unavailable"
    elif len(known)==2 and known[0]!=known[1]: direction="mixed"
    else: direction=known[0]
    return {"direction":direction,"available":direction!="unavailable","evidence":evidence,
            "note":"FII and DII are shown separately; participant counterpart positions are not summed."}

def futures_signal(data: dict | None) -> dict:
    if not data: return {"direction":"unavailable","available":False,"evidence":{}}
    rows=[x for x in data.get("underlyings",[]) if x.get("instrument_type")=="FUTIDX" and x.get("symbol")=="NIFTY"]
    # The NIFTY index future is the relevant index-level confirmation. Do not
    # substitute the stock-futures universe if it is absent.
    if not rows:
        return {"direction":"unavailable","available":False,"evidence":{"nifty_index_future":"missing"}}
    classes=[x.get("classification") for x in rows]
    bull=sum(x in ("long_buildup","short_covering") for x in classes)
    bear=sum(x in ("short_buildup","long_unwinding") for x in classes)
    direction="bullish" if bull>bear else "bearish" if bear>bull else "mixed"
    return {"direction":direction,"available":True,"evidence":{"nifty_index_future_classifications":classes,
            "bullish_contracts":bull,"bearish_contracts":bear}}

def options_signal(data: dict | None) -> dict:
    if not data: return {"direction":"unavailable","available":False,"evidence":{}}
    expiries=data.get("expiries",[])
    if not expiries: return {"direction":"unavailable","available":False,"evidence":{"reason":"no expiry rows"}}
    # Use the nearest listed expiry only; do not combine expiry OI.
    expiry=sorted(expiries,key=lambda x:str(x.get("expiry","9999")))[0]
    supports=expiry.get("support_levels",[]); resistances=expiry.get("resistance_levels",[])
    put=float(supports[0].get("put_oi",0)) if supports else 0.0
    call=float(resistances[0].get("call_oi",0)) if resistances else 0.0
    if not supports or not resistances or (put==0 and call==0): direction="unavailable"
    elif put>call: direction="bullish"
    elif call>put: direction="bearish"
    else: direction="neutral"
    return {"direction":direction,"available":direction!="unavailable",
            "evidence":{"expiry":expiry.get("expiry"),"highest_put_oi_support":supports[0] if supports else None,
                        "highest_call_oi_resistance":resistances[0] if resistances else None,
                        "max_pain":expiry.get("max_pain")},
            "note":"Compares top Put OI with top Call OI for the nearest listed expiry; descriptive only."}

def delivery_signal(data: dict | None) -> dict:
    if not data: return {"direction":"unavailable","available":False,"evidence":{}}
    rows=[x for x in data.get("stocks",[]) if isinstance(x.get("price_change_percentage"),(int,float))
          and isinstance(x.get("delivery_percentage"),(int,float))]
    if not rows: return {"direction":"unavailable","available":False,"evidence":{"reason":"no matched stock price/delivery rows"}}
    positive=sum(x["price_change_percentage"]>0 for x in rows)
    negative=sum(x["price_change_percentage"]<0 for x in rows)
    direction="bullish" if positive>negative else "bearish" if negative>positive else "neutral"
    return {"direction":direction,"available":True,"evidence":{"matched_stocks":len(rows),
            "positive_price_change_stocks":positive,"negative_price_change_stocks":negative,
            "median_delivery_percentage":data.get("universe_median_delivery_percentage")},
            "note":"Uses EOD EQ-stock breadth among rows with price and delivery values; delivery alone is not treated as buying."}

def combine(signals: dict) -> dict:
    weights={"participant":25,"futures":30,"options":30,"delivery":15}
    score=0.0; used=0; evidence={}
    for key,weight in weights.items():
        sig=signals[key]; d=sig.get("direction")
        if d not in ("bullish","bearish","neutral"): continue
        score += weight * (1 if d=="bullish" else -1 if d=="bearish" else 0)
        used += weight
        evidence[key]={"direction":d,"weight":weight}
    if used==0:
        return {"direction":"insufficient_data","confidence":"unrated","score":None,"coverage_percent":0,
                "evidence_weights":evidence}
    normalized=score/used
    direction="bullish" if normalized>=0.20 else "bearish" if normalized<=-0.20 else "neutral_or_mixed"
    # Confidence reflects both directional agreement and available evidence coverage.
    strength=abs(normalized)
    coverage=used/100
    confidence_score=round(100*strength*coverage)
    confidence="high" if confidence_score>=65 else "moderate" if confidence_score>=35 else "low"
    return {"direction":direction,"confidence":confidence,"confidence_score":confidence_score,
            "score":round(normalized,4),"coverage_percent":round(coverage*100,1),"evidence_weights":evidence,
            "method":"Weighted signed agreement across available EOD signals, normalized by available weight. Missing sources reduce coverage and confidence; no unavailable source is imputed."}

def run(day_dir: Path, output: Path) -> dict:
    # Derive the date from the directory, and require all available snapshots to match it.
    trading_date=day_dir.name
    if len(trading_date)!=10 or trading_date[4]!="-": raise ValueError("Input directory must be data/analysis/YYYY-MM-DD")
    loaded={}
    for key,(filename,agent_name) in AGENTS.items():
        loaded[key]=read_snapshot(day_dir/filename,agent_name,trading_date)
    if not any(loaded.values()): raise ValueError("No passed Agent 2-5 snapshots found for this date")
    signals={"participant":participant_signal(loaded["participant"]),
             "delivery":delivery_signal(loaded["delivery"]),
             "futures":futures_signal(loaded["futures"]),
             "options":options_signal(loaded["options"])}
    summary=combine(signals)
    result={"schema_version":1,"agent":"eod_market_confluence_intelligence","status":"passed",
            "trading_date":trading_date,"data_scope":"EOD market-direction confluence only",
            "direction":summary["direction"],"confidence":summary["confidence"],
            "confidence_score":summary.get("confidence_score"),"score":summary.get("score"),
            "coverage_percent":summary["coverage_percent"],"signals":signals,
            "summary_method":summary.get("method"),"evidence_weights":summary["evidence_weights"],
            "source_agents":{k:bool(v) for k,v in loaded.items()},
            "limitations":["All inputs are end-of-day snapshots; this is not an intraday signal.",
            "Participant positioning, futures buildup, option OI and equity delivery describe different market dimensions.",
            "NIFTY index futures are required for futures confirmation; stock futures are not substituted.",
            "Options OI uses the nearest listed expiry only; expiries are not merged.",
            "Delivery breadth is an equity-universe proxy, not proof of NIFTY constituent buying.",
            "Confidence is a rule-based evidence-agreement grade, not a statistically calibrated probability.",
            "No prediction accuracy or profitability is claimed; separate chronological walk-forward backtesting is required."]}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return result

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--date",required=True,help="Trading date YYYY-MM-DD")
    p.add_argument("--analysis-root",default="data/analysis")
    p.add_argument("--output")
    a=p.parse_args()
    day=Path(a.analysis_root)/a.date
    out=Path(a.output or str(day/"eod_market_confluence_intelligence.json"))
    r=run(day,out)
    print(json.dumps({"status":r["status"],"trading_date":r["trading_date"],"direction":r["direction"],
        "confidence":r["confidence"],"confidence_score":r["confidence_score"],
        "coverage_percent":r["coverage_percent"],"output":str(out)},indent=2))
    return 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr); raise SystemExit(1)
