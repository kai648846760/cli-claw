from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompressionDecision:
    action: str
    reason: str


class CompressionPolicyEngine:
    def decide(self, estimated_tokens: int, trigger_tokens: int = 60000) -> CompressionDecision:
        if estimated_tokens >= trigger_tokens:
            return CompressionDecision(action="rotate", reason="threshold_exceeded")
        return CompressionDecision(action="noop", reason="below_threshold")
