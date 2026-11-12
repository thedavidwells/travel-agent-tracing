import json
import operator
import os
from datetime import date
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from flights import search_flights

load_dotenv()

search_tool = DuckDuckGoSearchRun()
tools = [search_tool, search_flights]
tools_by_name = {tool.name: tool for tool in tools}

if os.getenv("AI_GATEWAY_API_KEY"):
    model = ChatOpenAI(
        model="openai/gpt-4o",
        temperature=0,
        api_key=os.environ["AI_GATEWAY_API_KEY"],
        base_url="https://ai-gateway.vercel.sh/v1",
    )
else:
    model = ChatOpenAI(model="gpt-4o", temperature=0)
model_with_tools = model.bind_tools(tools)


class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]


def system_prompt() -> str:
    return (
        "You are a helpful travel assistant. You help users find flights, attractions, hotels, "
        "and other travel information.\n"
        f"Today's date is {date.today().isoformat()}. Resolve relative dates like "
        "'next Friday' to YYYY-MM-DD.\n"
        "- Use search_flights for any request to find, compare, or price flights. Convert city "
        "names to IATA airport codes. If the origin, destination, or departure date is missing, "
        "ask the user for it instead of guessing.\n"
        "- When search_flights returns no_results or invalid_input, explain why using its "
        "message and suggest a way forward.\n"
        "- Use the web search tool for everything else that needs current information, such as "
        "attractions, events, weather, and hotels."
    )


def llm_call(state: MessagesState) -> dict:
    """Call the LLM with the current messages and available tools."""
    return {
        "messages": [
            model_with_tools.invoke([SystemMessage(content=system_prompt())] + state["messages"])
        ]
    }


def tool_node(state: MessagesState) -> dict:
    """Execute tool calls from the last message."""
    result = []
    for tool_call in state["messages"][-1].tool_calls:
        tool = tools_by_name[tool_call["name"]]
        try:
            observation = tool.invoke(tool_call["args"])
        except Exception as exc:
            observation = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        if not isinstance(observation, str):
            observation = json.dumps(observation)
        result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
    return {"messages": result}


def should_continue(state: MessagesState) -> Literal["tool_node", "__end__"]:
    """Determine whether to continue to tool execution or end."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tool_node"
    return END


def build_agent():
    graph_builder = StateGraph(MessagesState)

    graph_builder.add_node("llm_call", llm_call)
    graph_builder.add_node("tool_node", tool_node)

    graph_builder.add_edge(START, "llm_call")
    graph_builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    graph_builder.add_edge("tool_node", "llm_call")

    # In-memory conversation history keyed by thread_id; it is lost when the server restarts.
    agent = graph_builder.compile(checkpointer=MemorySaver())
    return agent