# LangGraph Travel Agent

A LangGraph travel assistant exposed via a FastAPI server. It can search flights with a structured mock flight search tool and search the web using DuckDuckGo. It is traced with Arize Phoenix.

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

## Running Tests

```bash
poetry run pytest
```

## Project Structure

```
se-interview/
├── pyproject.toml       # Poetry dependencies
├── .env.example         # Environment variable template
├── docker-compose.yml   # Local Phoenix with a persistent volume
├── README.md            # This file
├── agent.py             # LangGraph agent implementation
├── api.py               # FastAPI server and Phoenix tracing setup
├── flights.py           # search_flights tool (structured mock flight search)
└── tests/
    └── test_flights.py  # Unit tests for the flight search tool
```

## How It Works

1. The agent receives a user message via the `/chat` endpoint
2. It calls GPT-4o (directly or through the Vercel AI Gateway) with the conversation so far and two tools: `search_flights` and DuckDuckGo web search
3. If the LLM calls a tool, the tool runs and its result is fed back to the LLM
4. The loop continues until the LLM provides a final response
5. The response is returned to the user

## Design Decisions: `search_flights` Tool

**Why a flight search tool.** The assistant's scenario covers attractions, flights, and hotels. Web search can describe attractions, but it can't return comparable flight options with prices, times, and stops. A dedicated tool fills that gap with output the model can reason over reliably.

**Why mock data.** The tool generates flights deterministically from the route, date, and cabin, so the same query always returns the same results. That keeps traces and evaluations reproducible, needs no extra API keys or rate limits, and makes failure cases (unknown airport, past date, over budget) easy to trigger on purpose. The tool's input and output schemas don't depend on the data source, so a real flight API could replace `find_flights` without changing the agent. The mock data is not realistic about geography: flight durations and prices are random per route, not based on distance.

**Structured input.** `FlightSearchInput` is a Pydantic model passed as the tool's `args_schema`, so the LLM sees typed fields with descriptions: IATA codes, ISO dates, passengers, cabin class, and an optional budget. Dates are accepted as strings and validated inside the tool, so a bad date becomes an `invalid_input` result the model can explain instead of an exception.

**Structured output.** The tool returns a `FlightSearchResult`:

```json
{
  "status": "ok | no_results | invalid_input",
  "query": { "origin": "SFO", "destination": "LIS", "depart_date": "2026-12-10", "...": "..." },
  "trip_type": "one_way | round_trip",
  "options": [
    {
      "airline": "Lufthansa",
      "flight_number": "LH8886",
      "depart_time": "2026-12-10T22:15",
      "arrive_time": "2026-12-11T02:28",
      "duration_minutes": 253,
      "stops": 0,
      "cabin": "economy",
      "price_per_passenger_usd": 590.55,
      "total_price_usd": 590.55
    }
  ],
  "message": "Found 5 one-way options."
}
```

Options are sorted by total price. Failures are returned as a `status` with a `message` instead of raised as errors. The model can then explain what went wrong ("no flights under $300; the cheapest is $412"), and the tool span in Phoenix shows exactly why the search failed.

**When the agent calls it.** The tool's docstring and the system prompt tell the model to use `search_flights` for any flight request and web search for everything else. The prompt includes today's date so relative dates like "next Friday" resolve correctly, and it tells the model to ask for a missing origin, destination, or date instead of guessing.

**Workflow integration.** The graph is unchanged: `llm_call`, then `tool_node` when the model requests tools, then back to `llm_call`. `tool_node` now serializes structured tool results to JSON for the `ToolMessage` and turns tool exceptions into an error result, so one failing tool (for example, a DuckDuckGo rate limit) doesn't fail the whole request.

**Observability.** No extra tracing code is needed. The OpenInference LangChain instrumentor records each call as a `TOOL` span with the model's arguments as input and the structured JSON as output.
