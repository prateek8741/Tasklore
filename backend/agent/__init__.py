"""Gap-closing agent (Google ADK): finds a plan's gaps, asks the right experts to record them, re-checks coverage.

    result = await agent.run_close_gaps(plan_id, member)
"""
from agent.runner import run_close_gaps

__all__ = ["run_close_gaps"]
