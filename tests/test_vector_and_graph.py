import json
from src.embeddings.indexer import build_chunk_text
from src.agent.state import AgentState, RouteDecision
from src.agent.graph import route_condition, build_tech_radar_graph
from langchain_core.messages import HumanMessage


def test_build_chunk_text():
    chunk = build_chunk_text(
        repo="pola-rs/polars",
        issue_number=100,
        title="Crash in lazy frame groupby",
        body="Reproduction snippet: df.group_by().agg() fails.",
        labels_raw=["bug", "crash"],
    )
    assert "Repository: pola-rs/polars | Issue #100" in chunk
    assert "Title: Crash in lazy frame groupby" in chunk
    assert "Labels: bug, crash" in chunk
    assert "Reproduction snippet" in chunk


def test_route_decision_schema():
    decision = RouteDecision(
        intent="vector",
        repo_filter="duckdb/duckdb",
        search_query="memory leak in buffer manager",
        reasoning="User is asking about a conceptual bug/leak symptom.",
    )
    assert decision.intent == "vector"
    assert decision.repo_filter == "duckdb/duckdb"
    assert decision.search_query == "memory leak in buffer manager"


def test_route_condition():
    state_sql = AgentState(messages=[HumanMessage(content="how many issues?")], route="sql")
    assert route_condition(state_sql) == "sql_retriever"

    state_vec = AgentState(messages=[HumanMessage(content="memory crash")], route="vector")
    assert route_condition(state_vec) == "vector_retriever"

    state_hyb = AgentState(messages=[HumanMessage(content="recent crashes")], route="hybrid")
    assert route_condition(state_hyb) == "hybrid_retriever"

    state_dir = AgentState(messages=[HumanMessage(content="hello")], route="direct")
    assert route_condition(state_dir) == "direct_responder"



def test_graph_compilation():
    graph = build_tech_radar_graph(checkpointer=None)
    assert graph is not None
