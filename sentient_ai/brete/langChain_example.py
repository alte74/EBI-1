"""Very simple LangChain chat example.

Install:
    pip install langchain langchain-anthropic python-dotenv

Put your API key in a .env file next to this script:
    ANTHROPIC_API_KEY=sk-ant-...
    ANTHROPIC_MODEL=claude-sonnet-4-5   (optional)

Run:
    python langChain_example.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

load_dotenv(Path(__file__).resolve().parent / ".env")

if not os.getenv("ANTHROPIC_API_KEY", "").strip():
    raise SystemExit("Missing ANTHROPIC_API_KEY. Add it to the .env file next to this script.")

llm = ChatAnthropic(
    model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    max_tokens=500,
)

messages = [SystemMessage(content="You are a friendly, concise assistant.")]

print("Chat with the LLM (type 'quit' to exit).")
while True:
    user_text = input("\nYou: ").strip()
    if user_text.lower() in {"quit", "exit"}:
        break
    if not user_text:
        continue

    messages.append(HumanMessage(content=user_text))
    reply = llm.invoke(messages)
    messages.append(AIMessage(content=reply.content))

    print(f"AI: {reply.content}")
