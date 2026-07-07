"""Programmatic usage — the same things the CLI does, from Python.

Run:  python -m examples.quickstart   (requires ANTHROPIC_API_KEY or `ant auth login`)
"""

from pydantic import BaseModel

from fastagent import Agent, load_config
import examples.tools  # noqa: F401  (registers @tool functions)


def main() -> None:
    config = load_config("fastagent.yaml")
    agent = Agent(
        config.get_agent("assistant"),
        on_text=lambda delta: print(delta, end="", flush=True),
    )

    print("agent> ", end="")
    result = agent.run("What's 1847 * 392, and what's the weather in Hyderabad?")
    print(f"\n\n[stop={result.stop_reason} tools={[c['name'] for c in result.tool_calls]}]")

    # Structured extraction into a typed model
    class Contact(BaseModel):
        name: str
        email: str

    contact = agent.extract("Extract the contact: reach Jane Doe at jane@example.com", Contact)
    print(f"extracted: {contact!r}")


if __name__ == "__main__":
    main()
