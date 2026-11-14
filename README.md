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

### Phoenix CLI and skills

I used the Phoenix CLI (`px`) and the Phoenix agent skills throughout this project to check traces from the terminal and give my coding agent Phoenix-aware context:

```bash
npx skills add Arize-ai/phoenix --skill phoenix-cli
px setup --no-input --endpoint http://localhost:6006 --project travel-agent
px trace list
px session get <session_id>
```

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

## Generating Traces and Exporting Spans

With Phoenix and the API running, run the scripted conversations:

```bash
poetry run python scripts/run_queries.py
```

The script sends 14 conversations (34 queries) through `/chat`. Each conversation uses its own session id, `run-<run_id>-<name>`, so it appears as one session in Phoenix. Some conversations are written to succeed and some to hit the agent's limits (unsupported airports, a budget below every fare, a request to book a hotel), so the user frustration evaluation has both outcomes to classify. A manifest of the run, with every turn and reply, is written to `exports/run_<run_id>.json`.

**These are synthetic users.** I wrote every conversation in `scripts/run_queries.py`, and the user turns are fixed in advance, so they don't react to what the agent actually says. That's on purpose: it makes runs repeatable and lets me plant known failures. The tradeoff is that real users phrase things differently, give up sooner or later, and hit failures I didn't think to script.

Export the run's spans with the Phoenix CLI. `px` reads its endpoint and project from `.env.phoenix`, which `px setup` creates:

```bash
px setup --no-input --endpoint http://localhost:6006 --project travel-agent
px span list exports/spans_run_<run_id>.json --since <run start time> --limit 5000 --include-annotations
```

The committed export `exports/spans_run_20261112T033521Z.json` holds 400 spans from 34 traces in 14 sessions: 53 LLM spans, 19 tool spans (13 `search_flights`, 6 `duckduckgo_search`), and 328 chain spans for the LangGraph steps.

### Findings from the traces

- **The agent blamed the wrong cause.** For "Los Angeles to Tokyo under $200", the agent chose Haneda (HND), which the mock data doesn't support. The tool returned `no_results` with "No flight data for HND", but the agent opened with "there are no flights … for under $200" before offering Narita (NRT). Its retry used NRT and silently dropped the budget. The trace shows the tool's exact message next to the agent's reply, which makes the mismatch obvious. A fix would be to list the supported airports in the tool description.
- **The agent overrode the tool with its own knowledge.** For "nonstop flights from Seattle to Lisbon", `search_flights` returned two nonstop options. When the user doubted them, the agent called the tool again, ran a web search, and then said there were no nonstop flights, contradicting the tool's data. Here the mock data is unrealistic, so the user was right. The trace shows the agent's answer is not grounded in the tool output.
- **Follow-ups reuse earlier results.** For the Chicago to London budget follow-ups ("under $2,000", then "under $3,000"), the agent filtered the earlier results from conversation memory instead of calling the tool again. That saves tool calls but relies on the earlier search having returned every option.
- **One question can trigger several tool calls.** "Compare economy and business" made two `search_flights` calls, one for each cabin.
- **Relative dates are ambiguous.** On Wednesday, Nov 11, "next Friday" was resolved to Nov 20, not Nov 13.

## Evaluating User Frustration

With Phoenix running and a trace run recorded, run:

```bash
poetry run python scripts/evaluate_frustration.py            # latest run in exports/
poetry run python scripts/evaluate_frustration.py --run-id 20261112T033521Z
```

The script uses Phoenix Evals (`arize-phoenix-evals`) and the Phoenix client (`arize-phoenix-client`). It needs no tracing changes in the app because it reads everything it needs from the spans already in Phoenix.

### Methodology

1. **Rebuild the conversations from spans.** The script pulls the run's spans from Phoenix and groups them by `session.id`. Each trace is one turn. The user message comes from the first LLM span's `llm.input_messages` and the assistant reply from the last LLM span's `llm.output_messages`. Ordering turns by start time gives a `User:` / `Assistant:` transcript for each session.
2. **Judge the whole session for frustration.** Frustration is a property of the conversation, not of one LLM call. A single reply like "No flights found" can be fine, and the frustration only shows when the user says "This is useless. Forget it." The `user_frustration` evaluator is a Phoenix Evals `ClassificationEvaluator` with labels `frustrated` (score 1) and `ok` (score 0). Its prompt is adapted from Phoenix's user frustration template and keeps that template's key rule: if the user was frustrated earlier but satisfied by the end, they are not frustrated. The judge also writes an explanation.
3. **Judge each follow-up turn for friction.** Phoenix's pre-built `UserFrictionEvaluator` labels whether a user message pushes back on the assistant's previous reply. It runs on every turn after the first, with the earlier transcript as context. This shows where in a conversation things went wrong.
4. **Use a different model as the judge.** The agent runs on GPT-4o. With `AI_GATEWAY_API_KEY` set, the judge is `anthropic/claude-sonnet-5.5` through the same gateway. With only `OPENAI_API_KEY`, it is `gpt-4.1`. Override it with `--judge-model`. A judge from another model family is less likely to share the agent's blind spots.
5. **Attach results to spans.** `user_frustration` is logged as a span annotation on the root `LangGraph` span of each session's final turn, because that is where the outcome is known. It is also logged as a session annotation for the Sessions view. `user_friction` is logged on the root span of each judged turn. Every annotation has a label, a score and the judge's explanation.
6. **Build a dataset of frustrated interactions.** The script queries Phoenix with the span filter `annotations['user_frustration'].label == 'frustrated'` and creates the dataset `frustrated-interactions-<run_id>`. Each example has the conversation and final user message as input, the agent's final reply as output, and the session, scenario and judge explanation as metadata. Each example links back to its source span.
7. **Check the judge.** The scripted conversations were written to produce known outcomes, so the script compares the judge's labels against them. Results are written to `exports/eval_<run_id>.json`.

### Results for run `20261112T033521Z`

The session judge agreed with the designed label on all 14 sessions. Four were labeled `frustrated`: the unsupported Bali airport, the repeated PDX misunderstanding, the hotel booking request, and the distrusted Seattle to Lisbon results. The Tokyo budget conversation was correctly labeled `ok`. The user said "Ugh, nothing at all?" but ended with "Okay, that works. Thanks.", and the judge's explanation cites that recovery. The friction judge flagged 6 of the 20 follow-up turns, all inside the four frustrated sessions.

Fourteen scripted sessions are enough to show the workflow, not to measure judge accuracy. Production use would need a larger, human-labeled sample to measure precision and recall before trusting the labels.

**Hand labels.** The 14/14 agreement above is against labels I designed, so it mostly shows the judge read the scripts the way I meant them. As a separate check, I read 6 sessions cold, labeled them myself before looking at the judge, and agreed with it on 5 of 6. The one we disagreed on was the warm-January session. I labeled it frustrated because the agent suggested Florida and the Caribbean to someone asking for ideas from the West Coast. The user never pushed back, so the judge said ok, and by the template's rule that's correct. It shows the limit of a frustration eval: it measures how the user reacted, not whether the answer was good. A relevance or correctness eval would catch this.

### Viewing results in Phoenix

- **Spans:** in the `travel-agent` project, filter spans with `annotations['user_frustration'].label == 'frustrated'`. Selecting a span shows the annotation and the judge's explanation in the Annotations tab.
- **Datasets:** the `frustrated-interactions-<run_id>` dataset holds the four examples. From the span filter above you can also select spans and use **Add to dataset** to build a dataset by hand.
- **Sessions:** the Sessions tab shows each conversation with its `user_frustration` session annotation.

## Limitations

- **Synthetic users.** The conversations are scripted, so the user turns never adapt to the agent. A real eval would sample production sessions.
- **Small sample.** 14 sessions is enough to show the workflow (trace, evaluate, annotate, build a dataset), not to measure judge precision or recall.
- **"ok" doesn't mean satisfied.** The judge only says the user wasn't left frustrated. A user who quietly leaves after a bad answer would likely score `ok`.
- **Mock flight data.** Prices and durations are deterministic but not based on distance, so some flights are unrealistic (a four-hour nonstop from SFO to Lisbon). One frustrated session exists because of this.
- **In-memory sessions.** `MemorySaver` loses conversation history when the API restarts. Production would use a persistent checkpointer like SQLite or Postgres.
- **One judge, one prompt.** Before trusting it in production, I'd check it against a larger set of human labels and watch for drift when the prompt or model changes.

## What I'd Do Next

- Fix the two grounding issues the traces surfaced: list the supported airports in the `search_flights` description, and tell the agent not to override tool results with its own knowledge.
- Re-run the `frustrated-interactions-<run_id>` dataset as a Phoenix experiment after each fix, so a change has to beat the old version on the same failing cases.
- Add a tool-calling correctness eval (right tool, right arguments) and an answer relevance eval to cover the other components of the agent.
- Swap the scripted users for an LLM-driven simulated user, and then for sampled real sessions.

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
├── scripts/
│   ├── run_queries.py           # Scripted conversations that generate traces
│   └── evaluate_frustration.py  # User frustration evals, span annotations, dataset
├── exports/             # Run manifests, exported spans, and eval results
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
