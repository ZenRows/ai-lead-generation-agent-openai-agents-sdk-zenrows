import asyncio

from agents import Agent, Runner, set_tracing_disabled

from tools import discover_leads, qualify_lead

set_tracing_disabled(True)

SOURCE_URL = "https://clutch.co/it-services"

ICP = (
    "IT services agencies that build custom software for B2B clients, "
    "publish detailed case studies with named clients, and offer ai and cloud services"
)

INSTRUCTIONS = """You find and qualify sales leads from web directories.

Follow this sequence exactly:

1. call discover_leads on the source url the user gives you
2. for each lead that has a website, call qualify_lead with its company, website,
   and the icp description from the user message
3. return the scored leads as a json array sorted by score descending

rules:
- process only the first 10 leads that have a website, then stop and return them
- skip leads with no website, they cannot be scored fairly
- if a tool returns a string starting with FETCH_ERROR, follow the instruction in
  that message. do not retry a call the message tells you not to retry
- never invent a lead, a website, or a score. every value comes from a tool result
"""

agent = Agent(
    name="lead generation agent",
    instructions=INSTRUCTIONS,
    model="gpt-4o-mini",  # the reasoning here is scoring, not planning
    tools=[discover_leads, qualify_lead],
)


async def main():
    result = await Runner.run(
        agent,
        input=f"source url: {SOURCE_URL}\n\nideal customer profile:\n{ICP}",
        max_turns=50,  # one qualify call per lead, plus discovery and the reply
    )

    # the trace shows which tools ran and in what order
    for item in result.new_items:
        if item.type == "tool_call_item":
            print(item.raw_item.name)

    print()
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())