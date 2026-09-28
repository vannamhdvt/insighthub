"""Mutation executor với identity RIÊNG (ServiceAccount chatops-scaler).

Identity đọc (MCP read-only) không bao giờ được dùng để ghi. Kubeconfig scaler chỉ có quyền
get/patch `deployments/scale` cho đúng 3 Deployment (xem k8s/rbac.yaml).
Không nhận tham số tự do: deployment/replicas đã được PermissionPolicy validate và approval bind.
"""
from __future__ import annotations

import asyncio
import re
from typing import Protocol

_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class ExecutionError(Exception):
    pass


class Scaler(Protocol):
    async def scale(self, deployment: str, replicas: int) -> str: ...


class KubectlScaler:
    identity = "chatops-scaler"

    def __init__(self, kubectl: str, kubeconfig: str, namespace: str, timeout: float = 20.0) -> None:
        self.kubectl = kubectl
        self.kubeconfig = kubeconfig
        self.namespace = namespace
        self.timeout = timeout

    async def scale(self, deployment: str, replicas: int) -> str:
        if not self.kubeconfig:
            raise ExecutionError("mutation identity not configured (CHATOPS_SCALER_KUBECONFIG)")
        if not _NAME.match(deployment) or not isinstance(replicas, int):
            raise ExecutionError("invalid scale arguments")
        proc = await asyncio.create_subprocess_exec(
            self.kubectl, "--kubeconfig", self.kubeconfig, "-n", self.namespace,
            "scale", f"deployment/{deployment}", f"--replicas={replicas}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ExecutionError("kubectl scale timed out") from exc
        if proc.returncode != 0:
            raise ExecutionError(" ".join(err.decode(errors="replace").split())[:200] or "kubectl scale failed")
        return " ".join(out.decode(errors="replace").split())[:200]
