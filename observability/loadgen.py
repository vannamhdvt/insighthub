#!/usr/bin/env python3
"""Steady lab traffic for the Day 4 baseline (>= 1h before any drill).

Standard library only. Every CHAT_EVERY seconds: POST /chat. Every UPLOAD_EVERY
seconds: upload a small unique markdown document. Prints one line per minute.

    python3 observability/loadgen.py --api-url http://localhost:18000
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.request
import uuid

QUESTIONS = [
    "InsightHub có những thành phần chính nào?",
    "Ingestion worker làm gì?",
    "Redis dùng để làm gì trong InsightHub?",
    "Tài liệu ở trạng thái pending nghĩa là gì?",
]


def post(url, body, headers, timeout=60):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def chat(api):
    body = json.dumps({"question": random.choice(QUESTIONS)}).encode()
    return post(api + "/chat", body, {"Content-Type": "application/json"})


def upload(api):
    boundary = "lg" + uuid.uuid4().hex
    text = f"# Loadgen {uuid.uuid4()}\n\nInsightHub baseline document for Day 4 telemetry.\n"
    payload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"loadgen-{uuid.uuid4().hex}.md\"\r\n"
        "Content-Type: text/markdown\r\n\r\n" + text + f"\r\n--{boundary}--\r\n"
    ).encode()
    return post(api + "/documents", payload, {"Content-Type": "multipart/form-data; boundary=" + boundary})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default="http://localhost:18000")
    p.add_argument("--chat-every", type=float, default=5)
    p.add_argument("--upload-every", type=float, default=60)
    a = p.parse_args()
    api = a.api_url.rstrip("/")
    next_chat = next_upload = time.monotonic()
    minute, stats = time.monotonic(), {}
    while True:
        t = time.monotonic()
        if t >= next_chat:
            code = chat(api)
            stats[f"chat_{code}"] = stats.get(f"chat_{code}", 0) + 1
            next_chat = t + a.chat_every
        if t >= next_upload:
            code = upload(api)
            stats[f"upload_{code}"] = stats.get(f"upload_{code}", 0) + 1
            next_upload = t + a.upload_every
        if t - minute >= 60:
            print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(stats, sort_keys=True), flush=True)
            minute, stats = t, {}
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
