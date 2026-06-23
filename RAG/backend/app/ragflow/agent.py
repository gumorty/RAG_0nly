"""RAGFlow Agent Workflow Client.

RAGFlow agents are pre-built Canvas DAG workflows that combine:
  - LLM calls
  - Knowledge Base retrieval
  - Categorize / Switch (conditional branching)
  - Iteration / Loop
  - HTTP Invoke
  - Code execution

This module wraps the RAGFlow ``/agents/*`` REST API so our management
system can create and execute agent workflows for complex multi-step queries.

Optimization analysis — Agent Workflow:

1.  Problem: 简单问答走 Agent 增加延迟和成本
    Solution: 只有 collection.metadata_ 中设置了 ``agent_id`` 时才启用 Agent 通路

2.  Problem: Agent 需要预定义 DSL，灵活度不如 Chat Completions
    Solution: 使用 RAGFlow 的 agent/templates 预置模板，管理系统只选择模板即可

3.  Problem: Agent 执行结果难以追踪
    Solution: Agent 的 session 日志存储在 RAGFlow Redis，通过 ``get_agent_logs`` 可获取完整 trace

执行链路::

    管理系统 POST /chat
      │ agent_enabled + agent_id in metadata?
      ├── YES → RagflowAgentClient.execute_agent()
      │         └── POST /agents/chat/completions
      │              └── RAGFlow Canvas DAG 执行
      │                   ├── LLM 理解问题 → 分解步骤
      │                   ├── Retrieve 检索知识库
      │                   ├── LLM 分析/综合/推理
      │                   └── 返回 answer + reference
      │         存储 RetrievalTrace + Answer
      │
      └── NO  → ChatService._ask_with_ragflow_chat() (常规 Chat Completions)
"""

import logging
from typing import Any, Generator

from app.core.config import get_settings
from app.ragflow.client import RagFlowClient

logger = logging.getLogger(__name__)


class RagflowAgentClient:
    """Client for RAGFlow's agent workflow system.

    An Agent is a Canvas DAG — a directed graph of components (LLM, Retrieve,
    Categorize, Switch, etc.) that executes in topological order.

    Usage in management system:
        1. List available templates via ``list_agent_templates()``
        2. Create an agent instance via ``create_agent()``
        3. Store ``agent_id`` in ``collection.metadata_["ragflow_agent_id"]``
        4. When ``ragflow_enable_agent = True`` and agent_id exists on collection,
           ``ChatService.ask()`` dispatches to ``execute_agent()``
    """

    def __init__(self) -> None:
        self.client = RagFlowClient()

    # ------------------------------------------------------------------
    # Template management
    # ------------------------------------------------------------------

    def list_agent_templates(self) -> list[dict]:
        """GET /agents/templates — list pre-built agent workflow templates.

        Returns a list of template descriptors that can be used to create
        agent instances.  Each template has a ``dsl`` key containing the
        full Canvas DAG definition.
        """
        try:
            resp = self.client._request("GET", "/agents/templates")
            data = resp.get("data", [])
            if isinstance(data, dict):
                return data.get("templates", data.get("items", []))
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("Failed to list agent templates: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Agent instance lifecycle
    # ------------------------------------------------------------------

    def create_agent(
        self,
        name: str,
        dsl: dict,
        description: str = "",
    ) -> str:
        """POST /agents — create an agent instance from Canvas DSL.

        Args:
            name: Human-readable agent name.
            dsl: Full Canvas DAG definition::

                    {"components": {...}, "path": [...], "globals": {...}}

            description: Optional description.

        Returns:
            The created agent's ID (string).
        """
        payload = {
            "title": name[:128],
            "dsl": dsl,
            "description": description or "Created by LLMStart RAG management platform",
        }
        try:
            resp = self.client._request("POST", "/agents", json=payload)
            data = resp.get("data", {})
            agent_id = data.get("id", "")
            logger.info("Created agent '%s' (id=%s)", name, agent_id)
            return agent_id
        except Exception as exc:
            logger.error("Failed to create agent '%s': %s", name, exc)
            return ""

    def get_agent(self, agent_id: str) -> dict:
        """GET /agents/<agent_id> — get agent detail including DSL."""
        try:
            return self.client._request("GET", f"/agents/{agent_id}")["data"]
        except Exception as exc:
            logger.warning("Failed to get agent %s: %s", agent_id, exc)
            return {}

    def delete_agent(self, agent_id: str) -> None:
        """DELETE /agents/<agent_id> — remove an agent."""
        try:
            self.client._request("DELETE", f"/agents/{agent_id}")
        except Exception as exc:
            logger.warning("Failed to delete agent %s: %s", agent_id, exc)

    # ------------------------------------------------------------------
    # Agent session & execution
    # ------------------------------------------------------------------

    def create_agent_session(self, agent_id: str) -> dict:
        """POST /agents/<agent_id>/sessions — create a conversation session.

        Returns the session dict containing ``id``, used in execute_agent.
        """
        try:
            resp = self.client._request(
                "POST", f"/agents/{agent_id}/sessions",
                json={"name": "Management session"},
            )
            return resp.get("data", {})
        except Exception as exc:
            logger.error("Failed to create agent session for %s: %s", agent_id, exc)
            return {}

    def execute_agent(
        self,
        agent_id: str,
        session_id: str,
        messages: list[dict],
        stream: bool = False,
    ) -> dict | Generator[dict, None, None]:
        """POST /agents/chat/completions — execute an agent Canvas DAG.

        Args:
            agent_id: The agent instance ID.
            session_id: A session ID from ``create_agent_session``.
            messages: Chat history + current user message::

                [{"role": "user", "content": "..."}]

            stream: If True, yields SSE events.  Otherwise returns final dict.

        Returns:
            Non-streaming: dict with keys ``answer``, ``reference``.
            Streaming: generator yielding ``{"answer": "...", "reference": {...}, "final": bool}``
        """
        payload = {
            "agent_id": agent_id,
            "session_id": session_id,
            "messages": messages,
            "stream": stream,
        }

        try:
            if stream:
                return self.client._request_stream_events(
                    "POST", "/agents/chat/completions", json=payload,
                )
            resp = self.client._request(
                "POST", "/agents/chat/completions", json=payload,
            )
            return resp.get("data", {})
        except Exception as exc:
            logger.error("Agent execution failed: %s", exc)
            if not stream:
                return {"answer": f"**Agent execution error**: {exc}", "reference": {}, "final": True}
            yield {"answer": f"**Agent execution error**: {exc}", "reference": {}, "final": True}
            return

    def get_agent_session(self, agent_id: str, session_id: str) -> dict:
        """GET /agents/<agent_id>/sessions/<session_id> — get session state."""
        try:
            return self.client._request(
                "GET", f"/agents/{agent_id}/sessions/{session_id}",
            )["data"]
        except Exception as exc:
            logger.warning("Failed to get agent session: %s", exc)
            return {}

    def get_agent_logs(self, message_id: str) -> list[dict]:
        """GET /agents/logs/<message_id> — retrieve agent execution trace.

        The trace includes the progress, component names, and elapsed time
        for each DAG node that executed.
        """
        try:
            resp = self.client._request("GET", f"/agents/logs/{message_id}")
            return resp.get("data", [])
        except Exception as exc:
            logger.warning("Failed to get agent logs: %s", exc)
            return []


# ------------------------------------------------------------------
# Pre-built agent DSL templates
# ------------------------------------------------------------------

def build_qa_agent_dsl(
    system_prompt: str,
    dataset_ids: list[str],
    top_n: int = 8,
) -> dict:
    """Build a simple QA agent Canvas DSL.

    The DAG flow::

        Begin → LLM (analyze question) → Retrieve (search KB) → LLM (answer) → Message (output)

    This is the minimal useful agent.  More complex agents add Categorize,
    Switch, Iteration, etc.
    """
    return {
        "components": {
            "begin": {
                "obj": "Begin",
                "param": {
                    "prologue": system_prompt,
                },
                "downstream": ["llm_analyze"],
            },
            "llm_analyze": {
                "obj": "LLM",
                "param": {
                    "llm_id": get_settings().ragflow_chat_model,
                    "system_prompt": "Analyze the user's question and identify key entities and retrieval requirements.",
                    "user_prompt": "{sys.query}",
                },
                "downstream": ["retrieval"],
            },
            "retrieval": {
                "obj": "Retrieval",
                "param": {
                    "top_n": top_n,
                    "dataset_ids": dataset_ids,
                    "similarity_threshold": 0.1,
                    "vector_similarity_weight": 0.3,
                    "empty_response": "",
                },
                "downstream": ["llm_answer"],
            },
            "llm_answer": {
                "obj": "LLM",
                "param": {
                    "llm_id": get_settings().ragflow_chat_model,
                    "system_prompt": "Answer the user's question strictly from the provided evidence. Add citation markers [1] after key facts.",
                    "user_prompt": "Question: {sys.query}\n\nEvidence:\n{{retrieval.output}}",
                    "cite": True,
                },
                "downstream": ["message"],
            },
            "message": {
                "obj": "Message",
                "param": {},
                "downstream": [],
            },
        },
        "path": ["begin"],
        "globals": {
            "query": "{sys.query}",
            "user_id": "{sys.user_id}",
            "date": "{sys.date}",
        },
    }
