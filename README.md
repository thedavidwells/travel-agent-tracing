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

## Running the API

Start the FastAPI server:

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
  "response": "Based on my search, here are the latest developments in AI..."
}
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
