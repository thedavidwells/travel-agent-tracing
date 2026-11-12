# LangGraph Agent with Web Search

A simple LangGraph agent that can search the web using DuckDuckGo, exposed via a FastAPI server.

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/docs/#installation)
- An OpenAI API key or a Vercel AI Gateway API key

## Setup

1. Install dependencies:

```bash
poetry install
```

2. Create a `.env` file from the example:

```bash
cp .env.example .env
```

3. Add one API key to the `.env` file, without quotes. With an OpenAI key, the agent calls OpenAI directly:

```
OPENAI_API_KEY=your_actual_api_key
```

With a Vercel AI Gateway key, it routes through the gateway instead. If both are set, the gateway key is used:

```
AI_GATEWAY_API_KEY=your_actual_api_key
```

## Running Phoenix

Start Phoenix with Docker. Traces are stored in the `phoenix_data` volume, so they survive restarts:

```bash
docker compose up -d
```

The Phoenix UI is at `http://localhost:6006`. Authentication is disabled in `docker-compose.yml` for local development. The agent sends its traces to the `travel-agent` project.

Use `docker compose down` to stop Phoenix and keep your traces. `docker compose down -v` deletes them.

## Running the API

Start Phoenix first, then start the FastAPI server:

```bash
poetry run uvicorn api:app --reload
```

The API will be available at `http://localhost:8000`.

## API Endpoints

### POST /chat

Send a message to the agent and receive a response.

**Request:**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the latest news about AI?"}'
```

**Response:**

```json
{
  "response": "Based on my search, here are the latest developments in AI...",
  "session_id": "4f4016bb-3849-4a95-a7bd-989bbeb53867"
}
```

To continue the same conversation, send the returned `session_id` with the next message. The agent remembers earlier turns in that session, and Phoenix groups them in the project's Sessions tab. Conversation memory is held in the server process, so it is cleared when the server restarts.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What about hotels there?", "session_id": "4f4016bb-3849-4a95-a7bd-989bbeb53867"}'
```

### GET /health

Health check endpoint.

```bash
curl http://localhost:8000/health
```


## Project Structure

```
se-interview/
├── pyproject.toml   # Poetry dependencies
├── .env.example     # Environment variable template
├── README.md        # This file
├── agent.py         # LangGraph agent implementation
└── api.py           # FastAPI server
```

## How It Works

1. The agent receives a user message via the `/chat` endpoint
2. It calls GPT-4o (directly or through the Vercel AI Gateway) with the message and available tools (DuckDuckGo search)
3. If the LLM decides to search, it executes the search and feeds results back
4. The loop continues until the LLM provides a final response
5. The response is returned to the user
