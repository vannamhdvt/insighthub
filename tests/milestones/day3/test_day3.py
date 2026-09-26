"""Day 3 milestone contract: policy-as-code gate + Helm deployment shape.

Runs offline against the repository (INSIGHTHUB_REPO_ROOT): Conftest policies are
evaluated on committed Terraform-plan fixtures (valid/unsafe/prod), Rego unit
tests run with `conftest verify`, and the Helm chart is rendered for EKS dev.
Missing tools are failures, not skips: the verifier treats skips as incomplete.
Real AWS plans are checked by the same policies in .github/workflows/iac.yml.
"""

import os
import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(os.environ.get("INSIGHTHUB_REPO_ROOT", ".")).resolve()
POLICY_DIR = REPO_ROOT / "infra" / "policies" / "terraform"
FIXTURES = REPO_ROOT / "infra" / "policies" / "fixtures"
CHART = REPO_ROOT / "infra" / "helm" / "insighthub"


def run(argv, timeout=120):
    tool = shutil.which(argv[0])
    assert tool, f"required tool not installed: {argv[0]}"
    return subprocess.run([tool, *argv[1:]], capture_output=True, text=True,
                          timeout=timeout, cwd=REPO_ROOT)


def conftest(plan):
    return run(["conftest", "test", "--no-color", "--policy", str(POLICY_DIR), str(FIXTURES / plan)])


def test_policy_allows_valid():
    result = conftest("plan-valid.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failures" in result.stdout
    # The dev public endpoint is surfaced, not silently accepted.
    assert "WARN" in result.stdout and "public EKS endpoint" in result.stdout


def test_policy_denies_unsafe():
    result = conftest("plan-unsafe.json")
    assert result.returncode == 1, result.stdout + result.stderr
    expected = [
        "RDS storage must be encrypted",
        "RDS must not be publicly accessible",
        "transit_encryption_enabled must be true",
        "port 5432 open to the internet",
        "IMDSv2",
        "missing required tags [\"owner\"]",
        "customer-managed KMS key",
        "IAM users/access keys are forbidden",
        "db.r6g.2xlarge not allowed in dev",
    ]
    missing = [m for m in expected if m not in result.stdout]
    assert not missing, f"policies did not report {missing}:\n{result.stdout}"


def test_policy_prod_guardrails():
    result = conftest("plan-prod-lab-settings.json")
    assert result.returncode == 1, result.stdout
    for message in ("prod RDS requires multi_az", "prod RDS requires deletion_protection",
                    "backup retention must be >= 7", "prod EKS public endpoint"):
        assert message in result.stdout, result.stdout


def test_policy_rego_unit_tests():
    result = run(["conftest", "verify", "--no-color", "--policy", str(POLICY_DIR)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failures" in result.stdout


def test_helm_schema_matches_compose_schema():
    # The chart ships its own copy (Helm cannot read files outside the chart).
    assert (CHART / "files" / "init.sql").read_bytes() == (REPO_ROOT / "infra" / "db" / "init.sql").read_bytes()


def render_dev():
    result = run(["helm", "template", "insighthub", str(CHART), "--namespace", "insighthub-dev",
                  "-f", str(CHART / "values-dev.yaml"), "--set", "ingress.tls.acmeEmail=ops@example.com"])
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def test_helm_dev_deploys_three_workloads_and_no_datastores():
    docs = render_dev()
    deployments = {d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"}
    assert deployments == {"insighthub-api", "insighthub-ingestion-worker", "insighthub-web"}
    # RDS/ElastiCache are managed services on EKS, never pods.
    assert not any("postgres" in name or "redis" in name for name in deployments)
    kinds = {d["kind"] for d in docs}
    assert {"HorizontalPodAutoscaler", "Ingress", "SecretProviderClass", "Job"} <= kinds
    ingress = next(d for d in docs if d["kind"] == "Ingress")
    assert ingress["spec"]["tls"], "HTTPS required"
    assert not any(d["kind"] == "Secret" for d in docs), "no plaintext Secret rendered for EKS"


def test_helm_pods_are_hardened_and_use_irsa_account():
    for doc in render_dev():
        if doc["kind"] not in {"Deployment", "Job"}:
            continue
        pod = doc["spec"]["template"]["spec"]
        assert pod["serviceAccountName"] == "insighthub"
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        for container in pod.get("initContainers", []) + pod["containers"]:
            ctx = container["securityContext"]
            assert ctx["readOnlyRootFilesystem"] is True
            assert ctx["allowPrivilegeEscalation"] is False
            assert ctx["capabilities"]["drop"] == ["ALL"]
            assert "limits" in container["resources"]
