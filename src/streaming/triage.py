import logging
import re
from typing import Any
from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent, TriageResult, TriageCategory, AlertSeverity

logger = logging.getLogger(__name__)

SEEK_SECURITY_KEYWORDS = ["cve-", "vulnerability", "exploit", "security", "rce", "injection", "overflow"]
SEEK_CRASH_KEYWORDS = ["segfault", "segmentation fault", "crash", "panic", "oom", "fatal error", "memory leak", "corruption"]
SEEK_BREAKING_KEYWORDS = ["breaking change", "regression", "deprecated", "removed in", "incompatible"]


class RealTimeTriager:
    """Classifies incoming GitHub issues using LLM structured output with rule-based fallback."""

    def __init__(self):
        self._llm = None
        if settings.gemini_api_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                self._llm = ChatGoogleGenerativeAI(
                    model="gemini-3.5-flash-lite",
                    google_api_key=settings.gemini_api_key,
                    temperature=0.0,
                    timeout=15.0,
                )
            except Exception as e:
                logger.warning(f"Could not initialize Gemini for real-time triage: {e}")

    def triage_heuristic(self, event: IncomingGitHubEvent) -> TriageResult:
        """Fast, robust deterministic heuristic triage used when LLM is unavailable."""
        combined_text = f"{event.title} {event.body} {' '.join(event.labels)}".lower()

        # Check CVE / Security
        if any(kw in combined_text for kw in SEEK_SECURITY_KEYWORDS):
            category: TriageCategory = "Security/CVE"
            severity: AlertSeverity = "critical"
            is_alert = True
        # Check Crash / Memory leak
        elif any(kw in combined_text for kw in SEEK_CRASH_KEYWORDS):
            category = "Critical Bug/Crash"
            severity = "critical" if ("leak" in combined_text or "segfault" in combined_text) else "high"
            is_alert = True
        # Check Breaking change / Regression
        elif any(kw in combined_text for kw in SEEK_BREAKING_KEYWORDS):
            category = "Breaking Change"
            severity = "high"
            is_alert = True
        else:
            category = "Noise/Question"
            severity = "low"
            is_alert = False

        # Version extraction regex heuristic (e.g. v1.2.3, 0.20.0, etc.)
        version_match = re.search(r"\b(?:v\d+\.\d+(?:\.\d+)?|\d+\.\d+\.\d+)\b", combined_text)
        affected_versions = version_match.group(0) if version_match else None

        # Workaround regex heuristic
        workaround = None
        if "workaround" in combined_text or "downgrade" in combined_text or "fix:" in combined_text:
            workaround = "Workaround or mitigation discussed in issue content."

        return TriageResult(
            category=category,
            severity=severity,
            summary=f"[{category}] {event.title}",
            affected_versions=affected_versions,
            workaround=workaround,
            is_alert_worthy=is_alert,
        )

    async def triage_event(self, event: IncomingGitHubEvent) -> TriageResult:
        """Analyzes and classifies the incoming event."""
        if not self._llm:
            return self.triage_heuristic(event)

        prompt = f"""You are an automated triage classifier for open-source library health.
Analyze the following GitHub issue and classify it.

Repository: {event.repo}
Issue #{event.issue_number}: {event.title}
Labels: {', '.join(event.labels)}
Content:
{(event.body or '')[:2000]}

Categorize strictly into one of:
- "Critical Bug/Crash"
- "Security/CVE"
- "Breaking Change"
- "Noise/Question"

Assign severity: "critical", "high", "medium", or "low".
Extract any affected library versions and any discussed workarounds or patches.
Mark is_alert_worthy=True if severity is critical or high.
"""
        try:
            structured_llm = self._llm.with_structured_output(TriageResult)
            result = await structured_llm.ainvoke(prompt)
            if isinstance(result, TriageResult):
                return result
            return self.triage_heuristic(event)
        except Exception as e:
            logger.debug(f"LLM triage fallback invoked due to error: {e}")
            return self.triage_heuristic(event)
