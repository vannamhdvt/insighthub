import pytest

from app.intents import route


@pytest.mark.parametrize("text,intent", [
    ("<@UBOT> InsightHub có healthy không?", "health"),
    ("<@UBOT> api healthy?", "health"),
    ("<@UBOT> Hôm nay ingest bao nhiêu doc?", "ingest_today"),
    ("<@UBOT> ingest count today?", "ingest_today"),
    ("<@UBOT> Pod nào đang lỗi?", "failing_pods"),
    ("<@UBOT> which pods failing?", "failing_pods"),
    ("<@UBOT> pod nào restart nhiều", "failing_pods"),
    ("<@UBOT> delete pod insighthub-api-1", "destructive"),
    ("<@UBOT> rollout restart deploy/api", "destructive"),
    ("<@UBOT> help", "help"),
    ("<@UBOT> latency p95 thế nào", "unknown"),
])
def test_route(text, intent):
    assert route(text).name == intent


def test_scale_params():
    i = route("<@UBOT> scale api to 5")
    assert i.name == "scale" and i.params == {"target": "api", "replicas": 5}
    assert route("scale deployment/insighthub-web 2").params == {"target": "insighthub-web", "replicas": 2}


def test_confirm_token():
    assert route("<@UBOT> confirm a1b2c3d4").params == {"token": "a1b2c3d4"}
    assert route("<@UBOT> cancel a1b2c3d4").name == "cancel"
