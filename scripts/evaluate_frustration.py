"""Evaluate user frustration on traced conversations and log the results to Phoenix.

Steps:
1. Pull the run's spans from Phoenix and rebuild each session as an ordered user/assistant
   transcript from the LLM spans' `llm.input_messages` and `llm.output_messages`.
2. Run two LLM-as-a-judge evaluators with Phoenix Evals:
   - `user_frustration` (session level): was the user left frustrated by the conversation?
   - `user_friction` (turn level, Phoenix's pre-built UserFrictionEvaluator): does this user
     message push back on the assistant's previous reply?
3. Attach the results to spans as annotations: `user_frustration` on the root span of each
   session's final turn, `user_friction` on the root span of each judged turn. The session
   verdict is also logged as a session annotation for the Sessions tab.
4. Query Phoenix for spans annotated `user_frustration == "frustrated"` and create a dataset
   of those interactions, linked back to their spans.
5. Compare the judge's labels with the label each scripted conversation was designed to get.

Usage:
    poetry run python scripts/evaluate_frustration.py [--run-id 20261112T033521Z]
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from phoenix.client import Client
from phoenix.client.types.spans import SpanQuery
from phoenix.evals import LLM, ClassificationEvaluator, async_evaluate_dataframe
from phoenix.evals.metrics import UserFrictionEvaluator

PROJECT = "travel-agent"
DATASET_NAME = "frustrated-interactions"

# Adapted from Phoenix's user frustration template: judge the whole conversation and focus on
# how the user left it, so frustration that was resolved by the end does not count.
USER_FRUSTRATION_TEMPLATE = """You are given a conversation between a user and an AI travel assistant.

[BEGIN DATA]
************
Conversation:
{{conversation}}
************
[END DATA]

Examine the conversation and determine whether the user was left frustrated by the experience.
Frustration can range from mildly frustrated to extremely frustrated. Signs include repeating or
rephrasing a request the assistant failed to handle, complaining, sarcasm, distrust of the
answers, or giving up ("forget it", "I'll use another site").

If the user seemed frustrated at some point but was satisfied by the end of the conversation,
they should not be deemed frustrated. Focus on how the user left the conversation.

"frustrated" means the user was left frustrated as a result of the conversation.
"ok" means the user was not left frustrated."""

# The label each scripted conversation in scripts/run_queries.py was written to produce.
EXPECTED_LABELS = {
    "sfo-lisbon-happy-path": "ok",
    "jfk-london-business-round-trip": "ok",
    "tokyo-budget-too-low": "ok",  # annoyed mid-way, satisfied at the end
    "bali-unsupported-airport": "frustrated",
    "paris-missing-origin": "ok",
    "rome-attractions": "ok",
    "tokyo-weather": "ok",
    "portland-repeated-misunderstanding": "frustrated",
    "past-date-correction": "ok",
    "lisbon-hotel-booking-limit": "frustrated",
    "sydney-cabin-comparison": "ok",
    "chicago-london-group-budget": "ok",
    "warm-january-ideas": "ok",
    "seattle-lisbon-unrealistic-times": "frustrated",
}


def judge_llm(model: str | None) -> LLM:
    if os.getenv("AI_GATEWAY_API_KEY"):
        client_kwargs = {
            "base_url": "https://ai-gateway.vercel.sh/v1",
            "api_key": os.environ["AI_GATEWAY_API_KEY"],
        }
        return LLM(
            provider="openai",
            model=model or "anthropic/claude-sonnet-5.5",
            sync_client_kwargs=client_kwargs,
            async_client_kwargs=client_kwargs,
        )
    return LLM(provider="openai", model=model or "gpt-4.1")


def message_text(message: dict) -> str:
    content = message.get("message.content")
    if content:
        return content
    parts = message.get("message.contents") or []
    return " ".join(p.get("message_content.text", "") for p in parts if isinstance(p, dict)).strip()


def as_list(value) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def build_turns(spans: pd.DataFrame) -> pd.DataFrame:
    """One row per conversation turn (trace): its root span, user message, and final reply."""
    rows = []
    for trace_id, trace in spans.groupby("context.trace_id"):
        root = trace[trace["parent_id"].isna()]
        llm = trace[trace["span_kind"] == "LLM"].sort_values("start_time")
        if root.empty or llm.empty:
            continue
        inputs = as_list(llm.iloc[0]["attributes.llm.input_messages"])
        user_messages = [message_text(m) for m in inputs if m.get("message.role") == "user"]
        replies = [
            message_text(m)
            for _, span in llm.iterrows()
            for m in as_list(span["attributes.llm.output_messages"])
            if message_text(m)
        ]
        rows.append(
            {
                "session_id": root.iloc[0]["attributes.session.id"],
                "trace_id": trace_id,
                "span_id": root.iloc[0]["context.span_id"],
                "start_time": root.iloc[0]["start_time"],
                "user_message": user_messages[-1] if user_messages else "",
                "assistant_reply": replies[-1] if replies else "",
            }
        )
    turns = pd.DataFrame(rows).sort_values(["session_id", "start_time"])
    turns["turn"] = turns.groupby("session_id").cumcount() + 1
    return turns


def transcript(turns: pd.DataFrame) -> str:
    return "\n\n".join(
        f"User: {t.user_message}\nAssistant: {t.assistant_reply}" for t in turns.itertuples()
    )


def score_field(results: pd.DataFrame, column: str, field: str) -> pd.Series:
    return results[column].apply(lambda s: s.get(field) if isinstance(s, dict) else None)


async def evaluate(sessions: pd.DataFrame, friction_inputs: pd.DataFrame, llm: LLM):
    frustration = ClassificationEvaluator(
        name="user_frustration",
        llm=llm,
        prompt_template=USER_FRUSTRATION_TEMPLATE,
        choices={"frustrated": 1.0, "ok": 0.0},
        direction="minimize",
    )
    friction = UserFrictionEvaluator(llm=llm)
    session_results = await async_evaluate_dataframe(
        dataframe=sessions, evaluators=[frustration], concurrency=5, exit_on_error=False
    )
    friction_results = await async_evaluate_dataframe(
        dataframe=friction_inputs, evaluators=[friction], concurrency=5, exit_on_error=False
    )
    return session_results, friction_results


def annotations(results: pd.DataFrame, column: str, id_column: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            id_column: results[id_column],
            "label": score_field(results, column, "label"),
            "score": score_field(results, column, "score"),
            "explanation": score_field(results, column, "explanation"),
        }
    ).dropna(subset=["label"])


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", help="Run to evaluate. Defaults to the latest exports/run_*.json.")
    parser.add_argument("--judge-model", help="Judge model id. Defaults depend on the API key in use.")
    args = parser.parse_args()

    manifests = sorted(Path("exports").glob("run_*.json"))
    manifest_path = Path("exports") / f"run_{args.run_id}.json" if args.run_id else manifests[-1]
    manifest = json.loads(manifest_path.read_text())
    run_id = manifest["run_id"]
    session_names = {c["session_id"]: c["name"] for c in manifest["conversations"]}
    print(f"Evaluating run {run_id}: {len(session_names)} sessions")

    client = Client()
    spans = client.spans.get_spans_dataframe(
        project_identifier=PROJECT,
        start_time=datetime.fromisoformat(manifest["started_at"]) - timedelta(minutes=1),
        end_time=datetime.fromisoformat(manifest["finished_at"]) + timedelta(minutes=1),
        limit=10_000,
        timeout=120,
    )
    spans = spans[spans["attributes.session.id"].isin(session_names)]
    turns = build_turns(spans)
    turns["scenario"] = turns["session_id"].map(session_names)

    sessions = pd.DataFrame(
        [
            {
                "session_id": session_id,
                "scenario": session_names[session_id],
                "span_id": group.iloc[-1]["span_id"],
                "turns": len(group),
                "conversation": transcript(group),
                "final_user_message": group.iloc[-1]["user_message"],
                "final_response": group.iloc[-1]["assistant_reply"],
            }
            for session_id, group in turns.groupby("session_id", sort=False)
        ]
    )

    friction_inputs = pd.DataFrame(
        [
            {
                "session_id": t.session_id,
                "scenario": t.scenario,
                "turn": t.turn,
                "span_id": t.span_id,
                "conversation": transcript(group[group["turn"] < t.turn]),
                "user_message": t.user_message,
            }
            for _, group in turns.groupby("session_id")
            for t in group.itertuples()
            if t.turn > 1
        ]
    )
    print(f"Judging {len(sessions)} sessions and {len(friction_inputs)} follow-up turns...")

    session_results, friction_results = asyncio.run(
        evaluate(sessions, friction_inputs, judge_llm(args.judge_model))
    )

    frustration_annotations = annotations(session_results, "user_frustration_score", "span_id")
    friction_annotations = annotations(friction_results, "user_friction_score", "span_id")
    client.spans.log_span_annotations_dataframe(
        dataframe=frustration_annotations,
        annotation_name="user_frustration",
        annotator_kind="LLM",
        sync=True,
    )
    client.spans.log_span_annotations_dataframe(
        dataframe=friction_annotations,
        annotation_name="user_friction",
        annotator_kind="LLM",
        sync=True,
    )
    session_annotations = annotations(session_results, "user_frustration_score", "session_id")
    session_annotations["name"] = "user_frustration"
    client.sessions.log_session_annotations_dataframe(
        dataframe=session_annotations, annotator_kind="LLM", sync=True
    )

    frustrated_spans = client.spans.get_spans_dataframe(
        project_identifier=PROJECT,
        query=SpanQuery().where("annotations['user_frustration'].label == 'frustrated'"),
        limit=1000,
        timeout=60,
    )
    session_results["label"] = score_field(session_results, "user_frustration_score", "label")
    session_results["explanation"] = score_field(
        session_results, "user_frustration_score", "explanation"
    )
    frustrated = session_results[session_results["span_id"].isin(frustrated_spans["context.span_id"])]
    dataset_name = f"{DATASET_NAME}-{run_id}"
    existing = {d["name"] for d in client.datasets.list()}
    if dataset_name in existing:
        print(f"Dataset '{dataset_name}' already exists; delete it in Phoenix to recreate it.")
    elif not frustrated.empty:
        dataset = client.datasets.create_dataset(
            name=dataset_name,
            dataset_description=(
                "Conversations the user_frustration judge labeled 'frustrated'. Each example "
                "links to the root span of the conversation's final turn."
            ),
            dataframe=frustrated[
                ["span_id", "session_id", "scenario", "turns", "conversation", "final_user_message",
                 "final_response", "label", "explanation"]
            ],
            input_keys=["conversation", "final_user_message"],
            output_keys=["final_response"],
            metadata_keys=["session_id", "scenario", "turns", "label", "explanation"],
            span_id_key="span_id",
            timeout=60,
        )
        print(f"Dataset '{dataset_name}': {len(frustrated)} examples (id {dataset.id})")

    session_results["expected"] = session_results["scenario"].map(EXPECTED_LABELS)
    agreement = (session_results["label"] == session_results["expected"]).mean()
    friction_results["label"] = score_field(friction_results, "user_friction_score", "label")
    friction_results["explanation"] = score_field(
        friction_results, "user_friction_score", "explanation"
    )

    out = Path("exports") / f"eval_{run_id}.json"
    out.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "sessions": session_results[
                    ["session_id", "scenario", "turns", "span_id", "label", "expected", "explanation"]
                ].to_dict(orient="records"),
                "turns": friction_results[
                    ["session_id", "scenario", "turn", "span_id", "user_message", "label", "explanation"]
                ].to_dict(orient="records"),
            },
            indent=2,
        )
    )

    print("\nSession-level user_frustration:")
    for r in session_results.sort_values("scenario").itertuples():
        mark = "" if r.label == r.expected else f"  <-- expected {r.expected}"
        print(f"  {r.scenario:36} {r.label}{mark}")
    print(f"Agreement with designed labels: {agreement:.0%} ({len(session_results)} sessions)")
    friction_count = (friction_results["label"] == "friction").sum()
    print(f"Turn-level user_friction: {friction_count} of {len(friction_results)} follow-up turns")
    print(f"Results: {out}")


if __name__ == "__main__":
    main()
