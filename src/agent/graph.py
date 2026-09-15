import logging
from typing import Literal, Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END

from src.common.config import settings
from src.agent.state import AgentState, RouteDecision
from src.agent.tools import execute_readonly_sql, semantic_vector_search, get_table_schema

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """You are the Router for Tech Radar, an open-source dependency early-warning system.
Analyze the user's latest query and conversation history to select the optimal retrieval strategy:

Monitored repositories in database:
- duckdb/duckdb
- pola-rs/polars
- pydantic/pydantic

Available Routes:
1. 'direct': The user is saying hello, asking who you are, asking about your capabilities, or asking something that doesn't need GitHub repository data.
2. 'sql': Structured queries, counts, aggregations, issues by author, ranking by number of comments, filtering by exact state ('open'/'closed') or specific dates.
3. 'vector': Conceptual searches, bug descriptions, crashes, memory leaks, performance slowdowns, error messages, workarounds, or searching across languages.
4. 'hybrid': Complex queries that simultaneously require strict structured filters (e.g., date ranges, closed status, comment thresholds) AND conceptual/semantic search for specific bug symptoms.

Identify the target repo (if any) and produce a refined search_query for semantic search.
"""

SQL_GENERATOR_PROMPT = """You are an expert PostgreSQL engineer for Tech Radar.
Generate a single, read-only SQL query against table `raw_github_events` to answer the user's question.

Table Schema:
- id (BIGSERIAL PRIMARY KEY)
- repo (VARCHAR(255)) -- e.g. 'duckdb/duckdb', 'pola-rs/polars', 'pydantic/pydantic'
- issue_number (INT)
- title (TEXT)
- author (VARCHAR(255))
- state (VARCHAR(32)) -- 'open' or 'closed'
- labels (JSONB)
- body (TEXT)
- comments_count (INT)
- upstream_created_at (TIMESTAMPTZ)
- upstream_updated_at (TIMESTAMPTZ)

Rules:
1. ONLY produce a SELECT or WITH statement. No comments, no formatting markdown, just the raw SQL string.
2. Filter by `repo` if a specific library was requested.
3. Always include `repo`, `issue_number`, `title`, `state`, and `upstream_created_at`.
4. Use `LIMIT 5` or `LIMIT 10` to avoid huge payloads.
"""

SYNTHESIZER_PROMPT = """You are Tech Radar Assistant, an expert AI engineer monitoring open-source repositories.
Your goal is to answer the user's request based on the technical context retrieved from the database.

Guidelines:
1. Respond in English.
2. Synthesize key technical findings clearly: issue title, severity, symptoms, status (open/closed), and any discussed workarounds or causes.
3. For every issue referenced, you MUST include a direct GitHub link in the format:
   `https://github.com/<repo>/issues/<issue_number>`
4. If no relevant issues or data were found, clearly state that no records match the criteria in the monitored repositories.
5. Use markdown formatting with bullet points and bold highlights for readability.
"""


def extract_clean_text(content: Any) -> str:
    """Extracts plain text from string or structured content list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
        return "\n".join(texts)
    return str(content)


def get_llm(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model="gemini-3.5-flash-lite",
        google_api_key=settings.gemini_api_key,
        temperature=temperature,
    )


# --- Node Implementations ---

def router_node(state: AgentState) -> dict:
    """Classifies user intent and routes to the appropriate retrieval strategy."""
    llm = get_llm(temperature=0.0).with_structured_output(RouteDecision)
    last_user_msg = extract_clean_text(state.messages[-1].content) if state.messages else ""

    messages = [
        SystemMessage(content=ROUTER_PROMPT),
        HumanMessage(content=f"User question: {last_user_msg}"),
    ]
    try:
        decision: RouteDecision = llm.invoke(messages)
        return {
            "route": decision.intent,
            "repo_filter": decision.repo_filter,
            "search_query": decision.search_query or last_user_msg,
        }
    except Exception as e:
        logger.warning(f"Router failed, falling back to hybrid: {e}")
        return {
            "route": "hybrid",
            "repo_filter": None,
            "search_query": last_user_msg,
        }


def sql_retriever_node(state: AgentState) -> dict:
    """Generates and executes a SQL query to fetch structured issue data."""
    llm = get_llm(temperature=0.0)
    last_user_msg = extract_clean_text(state.messages[-1].content) if state.messages else ""

    prompt = f"User Request: {last_user_msg}\nRepo Filter: {state.repo_filter or 'Any'}"
    sql_msg = llm.invoke([
        SystemMessage(content=SQL_GENERATOR_PROMPT),
        HumanMessage(content=prompt),
    ])
    raw_sql = extract_clean_text(sql_msg.content)
    clean_sql = raw_sql.strip().replace("```sql", "").replace("```", "").strip()

    sql_results = execute_readonly_sql.invoke({"query": clean_sql})
    context_entry = f"### SQL Query Results:\n**Executed Query:** `{clean_sql}`\n**Data:**\n{sql_results}"

    existing_context = list(state.retrieved_context or [])
    existing_context.append(context_entry)
    return {"sql_query": clean_sql, "retrieved_context": existing_context}


def vector_retriever_node(state: AgentState) -> dict:
    """Executes pgvector semantic search using Gemini embeddings."""
    last_msg = extract_clean_text(state.messages[-1].content) if state.messages else ""
    search_phrase = state.search_query or last_msg
    vector_results = semantic_vector_search.invoke({
        "query": str(search_phrase),
        "repo": state.repo_filter,
        "top_k": 5,
    })

    context_entry = f"### Semantic Vector Search Results (pgvector):\n**Searched Concept:** {search_phrase}\n{vector_results}"
    existing_context = list(state.retrieved_context or [])
    existing_context.append(context_entry)
    return {"retrieved_context": existing_context}


def hybrid_retriever_node(state: AgentState) -> dict:
    """Combines both SQL and Vector retrieval for comprehensive context."""
    state_after_sql = sql_retriever_node(state)
    temp_state = AgentState(
        messages=state.messages,
        route=state.route,
        repo_filter=state.repo_filter,
        search_query=state.search_query,
        sql_query=state_after_sql.get("sql_query"),
        retrieved_context=state_after_sql.get("retrieved_context", []),
    )
    state_after_vector = vector_retriever_node(temp_state)
    return {
        "sql_query": state_after_sql.get("sql_query"),
        "retrieved_context": state_after_vector.get("retrieved_context", []),
    }


def direct_responder_node(state: AgentState) -> dict:
    """Provides direct responses for greetings, general inquiries, or non-data questions."""
    llm = get_llm(temperature=0.3)
    system_msg = SystemMessage(
        content=(
            "You are the assistant for Tech Radar & Dependency Early-Warning System. "
            "Respond politely in English explaining your capabilities (analyzing GitHub issues, "
            "investigating bugs/crashes across DuckDB, Polars, and Pydantic via SQL and pgvector semantic search)."
        )
    )
    response = llm.invoke([system_msg] + list(state.messages))
    return {"messages": [response]}


def synthesizer_node(state: AgentState) -> dict:
    """Synthesizes the retrieved data (SQL and/or Vector) into the final user answer."""
    llm = get_llm(temperature=0.2)
    context_text = "\n\n".join(state.retrieved_context)
    last_user_msg = extract_clean_text(state.messages[-1].content) if state.messages else ""

    messages = [
        SystemMessage(content=SYNTHESIZER_PROMPT),
        HumanMessage(
            content=(
                f"User Question: {last_user_msg}\n\n"
                f"Technical context retrieved from database:\n{context_text}\n\n"
                f"Formulate a complete response in English with technical details and GitHub links."
            )
        ),
    ]

    response = llm.invoke(messages)
    return {"messages": [response]}



# --- Workflow Graph Builder ---

def route_condition(state: AgentState) -> Literal["sql_retriever", "vector_retriever", "hybrid_retriever", "direct_responder"]:
    if state.route == "sql":
        return "sql_retriever"
    elif state.route == "vector":
        return "vector_retriever"
    elif state.route == "hybrid":
        return "hybrid_retriever"
    return "direct_responder"


def build_tech_radar_graph(checkpointer=None):
    """Builds and compiles the multi-node LangGraph workflow."""
    workflow = StateGraph(AgentState)

    # Register nodes
    workflow.add_node("router", router_node)
    workflow.add_node("sql_retriever", sql_retriever_node)
    workflow.add_node("vector_retriever", vector_retriever_node)
    workflow.add_node("hybrid_retriever", hybrid_retriever_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("direct_responder", direct_responder_node)

    # Add edges
    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router",
        route_condition,
        {
            "sql_retriever": "sql_retriever",
            "vector_retriever": "vector_retriever",
            "hybrid_retriever": "hybrid_retriever",
            "direct_responder": "direct_responder",
        },
    )

    workflow.add_edge("sql_retriever", "synthesizer")
    workflow.add_edge("vector_retriever", "synthesizer")
    workflow.add_edge("hybrid_retriever", "synthesizer")
    workflow.add_edge("synthesizer", END)
    workflow.add_edge("direct_responder", END)

    return workflow.compile(checkpointer=checkpointer)
