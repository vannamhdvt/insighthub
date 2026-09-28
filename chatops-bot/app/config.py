"""Runtime configuration. Everything comes from environment variables; no secret has a default."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent


def _csv(name: str) -> frozenset[str]:
    return frozenset(x.strip() for x in os.environ.get(name, "").split(",") if x.strip())


def _int(name: str, default: int, low: int, high: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}]")
    return value


@dataclass(frozen=True)
class Settings:
    signing_secret: str = ""
    bot_token: str = ""
    bot_user_id: str = ""
    # Tier 2 (write): only these Slack user IDs may request or approve a mutation.
    operator_ids: frozenset[str] = field(default_factory=frozenset)
    namespace: str = "insighthub-local"
    timezone: str = "Asia/Ho_Chi_Minh"
    audit_log: Path = BOT_ROOT / "chatops-audit.log"
    queue_db: Path = BOT_ROOT / "reports" / "chatops-queue.sqlite3"
    mcp_config: Path = BOT_ROOT / "mcp-servers.json"
    catalog: Path = BOT_ROOT / "service-catalog.yaml"
    # Mutation identity: separate kubeconfig bound to ServiceAccount chatops-scaler.
    scaler_kubeconfig: str = ""
    kubectl: str = "kubectl"
    signature_max_age: int = 300
    approval_ttl: int = 60
    worker_max_attempts: int = 3
    answer_deadline: int = 45
    tool_timeout: int = 15
    # Day 6: LLM only through the LiteLLM gateway with the bot's own virtual key.
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    llm_model: str = "chatops-agent"
    llm_max_steps: int = 4

    @property
    def llm_enabled(self) -> bool:
        return bool(self.litellm_base_url and self.litellm_api_key)


def load_settings() -> Settings:
    return Settings(
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
        bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
        bot_user_id=os.environ.get("SLACK_BOT_USER_ID", ""),
        operator_ids=_csv("CHATOPS_OPERATOR_IDS"),
        namespace=os.environ.get("CHATOPS_NAMESPACE", "insighthub-local"),
        timezone=os.environ.get("CHATOPS_TIMEZONE", "Asia/Ho_Chi_Minh"),
        audit_log=Path(os.environ.get("CHATOPS_AUDIT_LOG", str(BOT_ROOT / "chatops-audit.log"))),
        queue_db=Path(os.environ.get("CHATOPS_QUEUE_DB", str(BOT_ROOT / "reports" / "chatops-queue.sqlite3"))),
        mcp_config=Path(os.environ.get("CHATOPS_MCP_CONFIG", str(BOT_ROOT / "mcp-servers.json"))),
        catalog=Path(os.environ.get("CHATOPS_SERVICE_CATALOG", str(BOT_ROOT / "service-catalog.yaml"))),
        scaler_kubeconfig=os.environ.get("CHATOPS_SCALER_KUBECONFIG", ""),
        kubectl=os.environ.get("CHATOPS_KUBECTL", "kubectl"),
        signature_max_age=_int("CHATOPS_SIGNATURE_MAX_AGE", 300, 1, 300),
        approval_ttl=_int("CHATOPS_APPROVAL_TTL", 60, 10, 300),
        worker_max_attempts=_int("CHATOPS_WORKER_MAX_ATTEMPTS", 3, 1, 5),
        answer_deadline=_int("CHATOPS_ANSWER_DEADLINE", 45, 5, 120),
        tool_timeout=_int("CHATOPS_TOOL_TIMEOUT", 15, 1, 60),
        litellm_base_url=os.environ.get("LITELLM_BASE_URL", ""),
        litellm_api_key=os.environ.get("CHATOPS_LITELLM_KEY", ""),
        llm_model=os.environ.get("CHATOPS_LLM_MODEL", "chatops-agent"),
        llm_max_steps=_int("CHATOPS_LLM_MAX_STEPS", 4, 1, 6),
    )
