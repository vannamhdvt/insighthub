#!/usr/bin/env python3
"""Pull real, citable samples for one incident window from Prometheus.

The RCA contract (scripts/VERIFICATION_CONTRACT.md) requires every cited sample
to match a live query_range value at its timestamp. This tool reads the drill
window, queries the recording rules at 60s steps and writes the baseline, peak
and recovery samples plus the alert timeline. The coding agent then writes the
hypotheses from this evidence (evidence-first), never from memory.

    python3 observability/rca_evidence.py --prometheus-url http://localhost:9090 \
        --window evidence/day4/incident-1.window.json --out rca-reports/incident-1.evidence.json
"""

import argparse
import datetime as dt
import json
import urllib.parse
import urllib.request

SIGNALS = {
    "llm-latency": ["insighthub:llm_latency_seconds:p95_5m", "insighthub:rag_latency_seconds:p95_5m", "insighthub:http_errors:ratio_rate5m"],
    "queue-backlog": ["insighthub:queue_backlog", "insighthub:http_requests:rate5m", "insighthub:http_errors:ratio_rate5m"],
    "error-burst": ["insighthub:http_errors:ratio_rate5m", "insighthub:http_requests:rate5m", "insighthub:llm_latency_seconds:p95_5m"],
}
ALERT = {"llm-latency": "InsightHubLLMLatencyAnomaly", "queue-backlog": "InsightHubQueueBacklogAnomaly", "error-burst": "InsightHubErrorRateAnomaly"}


def ts(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def rfc3339(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_range(base, expr, start, end, step=60):
    q = urllib.parse.urlencode({"query": expr, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{base}/api/v1/query_range?{q}", timeout=30) as r:
        data = json.load(r)
    if data.get("status") != "success":
        raise SystemExit(f"Prometheus error for {expr}: {data}")
    return data["data"]["result"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prometheus-url", default="http://localhost:9090")
    p.add_argument("--window", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    base = a.prometheus_url.rstrip("/")
    w = json.load(open(a.window))
    ns, kind = w["namespace"], w["kind"]
    start, injected, recovered, end = ts(w["baseline_start"]), ts(w["injected_at"]), ts(w["recovered_at"]), ts(w["ended_at"])
    start, end = int(start) - int(start) % 60 + 60, int(end) - int(end) % 60
    samples, series_summary = [], []
    for metric in SIGNALS[kind]:
        for s in query_range(base, f'{metric}{{namespace="{ns}"}}', start, end):
            pts = [(float(t), float(v)) for t, v in s["values"] if v not in ("NaN", "+Inf", "-Inf")]
            if not pts:
                continue
            before = [x for x in pts if x[0] < injected]
            during = [x for x in pts if injected <= x[0] <= recovered]
            after = [x for x in pts if x[0] > recovered]
            picks = []
            if before:
                picks.append(("baseline", before[-1]))
            if during:
                picks.append(("peak", max(during, key=lambda x: x[1])))
            if after:
                picks.append(("recovery", after[-1]))
            for role, (t, v) in picks:
                samples.append({"metric": metric, "labels": {k: v2 for k, v2 in s["metric"].items() if k != "__name__"},
                                "timestamp": rfc3339(t), "value": v, "role": role})
            series_summary.append({"metric": metric, "min": min(v for _, v in pts), "max": max(v for _, v in pts), "points": len(pts)})
    alerts = query_range(base, f'ALERTS{{alertname="{ALERT[kind]}",namespace="{ns}",alertstate="firing"}}', start, end)
    firing = [float(t) for s in alerts for t, _ in s["values"]]
    out = {
        "incident_id": w["incident_id"],
        "kind": kind,
        "namespace": ns,
        "started_at": rfc3339(start),
        "ended_at": rfc3339(end),
        "injected_at": w["injected_at"],
        "recovered_at": w["recovered_at"],
        "alert": {"name": ALERT[kind], "first_firing_at": rfc3339(min(firing)) if firing else None,
                  "last_firing_at": rfc3339(max(firing)) if firing else None,
                  "detection_seconds": round(min(firing) - injected) if firing else None},
        "series_summary": series_summary,
        "samples": samples,
    }
    json.dump(out, open(a.out, "w"), indent=2)
    print(json.dumps(out["alert"]), f"{len(samples)} samples ->", a.out)


if __name__ == "__main__":
    main()
