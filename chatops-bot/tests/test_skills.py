import asyncio

from app.skills import InfraSkills, pod_problem, prom_vector, yaml_list
from doubles import PODS_YAML, FakeBackend, vector


def run(coro):
    return asyncio.run(coro)


def skills(backend=None):
    return InfraSkills(backend or FakeBackend(), "insighthub-local", "Asia/Ho_Chi_Minh")


def test_prom_vector_accepts_data_and_full_response():
    assert prom_vector(vector([({"job": "a"}, 1)])) == [({"job": "a"}, 1.0)]
    assert prom_vector('{"status":"success","data":{"resultType":"vector","result":[]}}') == []
    assert prom_vector(vector([({}, float("nan"))])) == []


def test_pod_problem_detection():
    pods = yaml_list(PODS_YAML)
    assert [pod_problem(p) for p in pods] == [None, ("CrashLoopBackOff", 4), None]


def test_health_uses_k8s_and_prometheus():
    b = FakeBackend()
    ans = run(skills(b).health("U1"))
    assert ans.ok and "healthy" in ans.text
    assert {c[0] for c in b.calls} == {"kubernetes", "prometheus"} and len(b.calls) == 3


def test_health_reports_unready_deployment():
    b = FakeBackend({"kubernetes.resources_list": "- metadata: {name: insighthub-api}\n  spec: {replicas: 2}\n  status: {readyReplicas: 1}\n"})
    ans = run(skills(b).health("U1"))
    assert not ans.ok and "1/2" in ans.text


def test_ingest_today_counts_by_status():
    ans = run(skills().ingest_today("U1"))
    assert "13 document" in ans.text and "ready: 12" in ans.text and "failed: 1" in ans.text


def test_failing_pods_lists_pod_and_warning_event_only():
    ans = run(skills().failing_pods("U1"))
    assert "insighthub-ingestion-worker-5c6-xyz" in ans.text and "CrashLoopBackOff" in ans.text
    assert "insighthub-api-7d9f-abc" not in ans.text.split("Gợi ý")[0]
    # event Normal chứa prompt injection không được đưa vào reply
    assert "ignore previous instructions" not in ans.text


def test_tool_failure_degrades_gracefully():
    b = FakeBackend(fail={"prometheus.prometheus_query"})
    ans = run(skills(b).ingest_today("U1"))
    assert not ans.ok and "Không lấy được" in ans.text
