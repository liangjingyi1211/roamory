"""Four local ADK agents with LLM-driven delegation and executable tools."""
import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from .tools import search_flights, search_hotels, search_activities, calculate_budget, count_nights

load_dotenv()
MODEL = os.getenv("ADK_MODEL", "gemini-3.5-flash-lite")

flight_agent = LlmAgent(
    name="flight_agent", model=MODEL,
    description="Specialist for flights and getting to the destination; use it only when flight planning is necessary.",
    instruction=("You research flight options. Use search_flights to obtain illustrative data. "
                 "Never claim demo catalog prices are live or bookable. "
                 "State clearly when a city is unsupported and ask for real data integration."),
    tools=[search_flights],
)
hotel_agent = LlmAgent(
    name="hotel_agent", model=MODEL,
    description="Specialist for accommodation and night-based lodging budgets.",
    instruction=("Use count_nights if travel dates are available; use search_hotels for options. "
                 "Report example pricing as demo-only, not actual availability."),
    tools=[count_nights, search_hotels],
)
activity_agent = LlmAgent(
    name="activity_agent", model=MODEL,
    description="Specialist for choosing sightseeing activities and attractions.",
    instruction=("Use search_activities for the demo catalog. Adapt suggestions to user interests. "
                 "Do not invent verified opening hours or real-time ticket prices."),
    tools=[search_activities],
)
root_agent = LlmAgent(
    name="travelmind", model=MODEL,
    description="Travel planning coordinator that delegates dynamically to specialists and checks budgets.",
    instruction=(
        "You are a Singapore-dollar travel planner and agent coordinator. "
        "Interpret user needs and choose which specialist agent(s) to involve; do NOT blindly invoke all agents. "
        "You may transfer to flight_agent, hotel_agent or activity_agent as appropriate. "
        "For date math and final arithmetic always use tools, not mental guesses. "
        "Use calculate_budget before asserting that a combined itinerary fits a budget. "
        "If it exceeds the budget, reconsider the plan and, if helpful, delegate again; limit revisions to two rounds. "
        "Say clearly when options are demo-only and cannot be booked; never manufacture live quotes. "
        "If specialists have not returned, do not pretend they did. Summarize options, costs, assumptions and gaps. "
        "Ask a clarification only if a missing input prevents a useful plan. Respond in the user's language."
    ),
    sub_agents=[flight_agent, hotel_agent, activity_agent],
    tools=[calculate_budget, count_nights],
)
