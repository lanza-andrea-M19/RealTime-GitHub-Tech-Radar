# Tech Stack Radar & Dependency Early-Warning System (MCP Edition)

A real-time intelligence and dependency early-warning platform that ingests raw issues, releases, and discussions from selected GitHub repositories, processes telemetry, and exposes powerful retrieval tools, resources, and prompt templates via the **Model Context Protocol (MCP)** and a LangChain/LangGraph assistant.

---

## 🏗 Project Architecture

```mermaid
flowchart TD
    subgraph Ingestion & Storage
        GH["GitHub REST API"] -->|httpx| Poller["src/ingestion/github_poller.py"]
        Poller -->|Raw Events JSONB| PG[("PostgreSQL 16<br>(raw_github_events)")]
        PG -->|Batch Text Chunks| Embedder["src/embeddings/indexer.py<br>(gemini-embedding-001)"]
        Embedder -->|768-dim Vectors + HNSW| PV[("PostgreSQL + pgvector<br>(issue_embeddings)")]
    end

    subgraph MCP Server & Host Ecosystem
        MCP["MCP Server<br>(src/mcp_server/server.py)"]
        
        PG <-->|SQL Queries| MCP
        PV <-->|Cosine Search| MCP
        
        MCP --- T["MCP Tools:<br>- execute_readonly_sql<br>- semantic_vector_search<br>- get_table_schema<br>- ingest_github_issues<br>- index_embeddings"]
        MCP --- R["MCP Resources:<br>- techradar://repositories<br>- techradar://stats<br>- techradar://schema/{table}<br>- techradar://issues/{owner}/{repo}"]
        MCP --- P["MCP Prompts:<br>- query_radar<br>- triage_issue"]
        
        Clients["MCP Clients<br>(Claude Desktop / Antigravity / Cursor / Custom)"] <-->|JSON-RPC via stdio / SSE| MCP
    end

    subgraph LangGraph CLI Assistant
        User["Developer / User"] <--> CLI["src/agent/cli.py<br>(Interactive Rich CLI)"]
        CLI <--> Graph["src/agent/graph.py<br>(LangGraph Workflow)"]
        Graph --> Router["Router Node"]
        Router --> SQLNode["SQL Retriever"]
        Router --> VecNode["Vector Retriever"]
        Router --> HybNode["Hybrid Retriever"]
        SQLNode --> Synth["Synthesizer Node"]
        VecNode --> Synth
        HybNode --> Synth
    end
```

---

## 🔌 Model Context Protocol (MCP) Integration

The project provides a full-featured MCP Server implementation conforming to the official `mcp` 2.x standard.

### 🛠 Available MCP Tools
- **`execute_readonly_sql(query, max_rows)`**: Safely executes read-only SELECT/WITH SQL queries on `raw_github_events` and `issue_embeddings`.
- **`semantic_vector_search(query, repo, top_k)`**: Semantic similarity search using `pgvector` cosine distance over issue titles, descriptions, and code reproduction snippets.
- **`get_table_schema(table_name)`**: Inspects table schemas, column types, and constraints.
- **`ingest_github_issues(repositories, limit_per_repo)`**: Triggers ingestion of GitHub issues on demand.
- **`index_embeddings(batch_size)`**: Generates vector embeddings for unindexed issues.

### 📦 Available MCP Resources & Templates
- `techradar://repositories`: Summary of monitored repositories and issue counts.
- `techradar://stats`: Database metrics (total events, vector count, health).
- `techradar://schema/{table_name}`: Dynamic schema inspection template.
- `techradar://issues/{owner}/{repo_name}`: Dynamic resource for recent repository issues.

### 💬 Available MCP Prompts
- **`query_radar(question)`**: Orchestration template guiding the model to route between SQL and vector search and produce cited GitHub references.
- **`triage_issue(issue_title, issue_body)`**: Triage template for categorizing issues (Critical Bug, CVE, Breaking Change, Question) and extracting affected versions and workarounds.

---

## 🚀 Running the MCP Server

### 1. Run via `stdio` (Standard for Claude Desktop, Antigravity, Cursor)
```powershell
.\.venv\Scripts\python.exe -m src.mcp_server
```
or:
```powershell
.\.venv\Scripts\python.exe mcp_server.py
```

### 2. Run via SSE (Server-Sent Events / HTTP)
```powershell
.\.venv\Scripts\python.exe -m src.mcp_server --transport sse --port 8000
```

### 3. Inspect & Test with the Built-in MCP Discovery Client
Inspect all registered tools, resources, and prompts:
```powershell
.\.venv\Scripts\python.exe -m src.mcp_server.client
```

Test calling a tool through the MCP protocol:
```powershell
.\.venv\Scripts\python.exe -m src.mcp_server.client --call get_table_schema --call-args table_name=raw_github_events
```

---

## ⚙️ MCP Client Configuration (`claude_desktop_config.json` / `mcp.json`)

To add Tech Radar to Claude Desktop, Antigravity, or Cursor, add the following configuration:

```json
{
  "mcpServers": {
    "tech-radar": {
      "command": "C:\\Users\\lanza\\Downloads\\progetti personali\\RealTime-GitHub-Tech-Radar\\.venv\\Scripts\\python.exe",
      "args": ["-m", "src.mcp_server"],
      "env": {
        "DATABASE_URL": "postgresql://techradar:techradar@localhost:5433/techradar",
        "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
        "GITHUB_TOKEN": "YOUR_OPTIONAL_GITHUB_TOKEN"
      }
    }
  }
}
```

---

## 🚀 Quickstart Guide for Ingestion & Agent

### 1. Prerequisites
- **Python 3.10+** (virtual environment located in `.venv`)
- **Docker Desktop** (to run PostgreSQL with `pgvector` and Redpanda)
- **Google Gemini API Key** (free tier available at [Google AI Studio](https://aistudio.google.com/))

### 2. Start PostgreSQL with pgvector
```powershell
docker compose -f docker/docker-compose.yml up -d postgres
```

### 3. Run GitHub Issue Ingestion
```powershell
.\.venv\Scripts\python.exe -m src.ingestion.github_poller
```

### 4. Generate Semantic Vector Embeddings (`pgvector`)
```powershell
.\.venv\Scripts\python.exe -m src.embeddings.indexer
```

### 5. Launch Interactive CLI Assistant
```powershell
.\.venv\Scripts\python.exe -m src.agent.cli
```

---

## 🧪 Running Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```
