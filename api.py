import uuid

from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from phoenix.otel import register, using_session
from pydantic import BaseModel

load_dotenv()

# Must run before the agent module is imported so LangChain is instrumented.
tracer_provider = register(project_name="travel-agent", auto_instrument=True)

from agent import build_agent  # noqa: E402

agent = build_agent()

app = FastAPI(
    title="LangGraph Agent API",
    description="A simple API for interacting with a LangGraph agent that can search the web",
    version="0.1.0",
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Send a message to the agent and get a response.

    Pass the returned session_id on later requests to continue the same conversation.
    """
    session_id = request.session_id or str(uuid.uuid4())
    with using_session(session_id):
        result = agent.invoke(
            {"messages": [HumanMessage(content=request.message)]},
            config={"configurable": {"thread_id": session_id}},
        )
    return ChatResponse(response=result["messages"][-1].content, session_id=session_id)


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
