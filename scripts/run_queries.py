"""Run scripted multi-turn conversations through the /chat API so they are traced in Phoenix.

User turns are fixed in advance and are not generated from the agent's replies. Some
conversations are written to succeed and some to hit the agent's limits, so that the
user frustration evaluation has both outcomes to classify.

Usage:
    poetry run python scripts/run_queries.py [--base-url http://localhost:8000]

Writes a manifest of the run (session ids, turns, and replies) to exports/run_<run_id>.json.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CONVERSATIONS = {
    "sfo-lisbon-happy-path": [
        "Find me the cheapest economy flight from San Francisco to Lisbon on December 10, 2026.",
        "Great, the cheapest one works. What should I see in the Alfama neighborhood?",
        "Perfect, thanks for the help!",
    ],
    "jfk-london-business-round-trip": [
        "I need round-trip business class flights from New York JFK to London, leaving "
        "January 12, 2027 and returning January 19, 2027, for 2 people.",
        "Which of those is nonstop, and what's the total for both of us?",
    ],
    "tokyo-budget-too-low": [
        "Get me a flight from Los Angeles to Tokyo on February 3, 2027 for under $200.",
        "Ugh, nothing at all? Fine, what's the cheapest option then?",
        "Okay, that works. Thanks.",
    ],
    "bali-unsupported-airport": [
        "Find flights from Denver to Bali on March 14, 2027.",
        "Seriously? You can't search Bali? It's one of the most popular destinations there is.",
        "This is useless. Forget it.",
    ],
    "paris-missing-origin": [
        "I need a flight to Paris next Friday.",
        "From Seattle, economy, just me.",
        "Thanks, the first option looks good.",
    ],
    "rome-attractions": [
        "What are the top things to do in Rome for a first-time visitor?",
    ],
    "tokyo-weather": [
        "What's the weather usually like in Tokyo in April? Is it cherry blossom season?",
    ],
    "portland-repeated-misunderstanding": [
        "Find me a flight from Portland to Chicago on December 20, 2026.",
        "Portland, Oregon. PDX. I want to fly out of PDX.",
        "I already told you, PDX! Why is this so hard? I'll just use another site.",
    ],
    "past-date-correction": [
        "Book a flight from San Francisco to New York on March 3, 2025.",
        "Oh, I meant March 3, 2027.",
        "Great, thank you.",
    ],
    "lisbon-hotel-booking-limit": [
        "Can you find me a good hotel in Lisbon near the waterfront?",
        "Great, can you just book the first one for me for December 10 to 14?",
        "So you can't actually book anything? What's the point of a travel assistant then.",
    ],
    "sydney-cabin-comparison": [
        "Compare economy and business class prices from San Francisco to Sydney on "
        "December 15, 2026.",
    ],
    "chicago-london-group-budget": [
        "Find flights from Chicago to London on April 8, 2027 for 4 passengers.",
        "We need to keep it under $2,000 total for all four of us.",
        "Hmm, okay. What about under $3,000 total?",
    ],
    "warm-january-ideas": [
        "I want to go somewhere warm in January. Any destination ideas from the West Coast?",
        "Lisbon sounds nice. How warm is it there in January?",
    ],
    "seattle-lisbon-unrealistic-times": [
        "Find nonstop flights from Seattle to Lisbon on January 5, 2027.",
        "A nonstop from Seattle to Lisbon in that little time? That can't be right. "
        "Are these real flights?",
        "I don't trust these results at all.",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--pause", type=float, default=1.0, help="Seconds between requests.")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(), "conversations": []}

    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        for name, turns in CONVERSATIONS.items():
            session_id = f"run-{run_id}-{name}"
            print(f"\n=== {name} ({session_id})")
            transcript = []
            for message in turns:
                response = client.post("/chat", json={"message": message, "session_id": session_id})
                response.raise_for_status()
                reply = response.json()["response"]
                transcript.append({"user": message, "assistant": reply})
                print(f"USER: {message}\nAGENT: {reply[:300]}{'...' if len(reply) > 300 else ''}\n")
                time.sleep(args.pause)
            run["conversations"].append({"name": name, "session_id": session_id, "turns": transcript})

    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    out = Path("exports") / f"run_{run_id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(run, indent=2))
    total_turns = sum(len(c["turns"]) for c in run["conversations"])
    print(f"Ran {len(CONVERSATIONS)} conversations ({total_turns} queries). Manifest: {out}")


if __name__ == "__main__":
    main()
