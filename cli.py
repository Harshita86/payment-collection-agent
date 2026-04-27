#!/usr/bin/env python3
"""Interactive CLI to run the payment collection agent."""

from agent import Agent


def main():
    print("=" * 60)
    print("  Payment Collection Agent  |  type 'quit' to exit")
    print("=" * 60)
    print()

    agent = Agent()
    response = agent.next("Hello")
    print(f"Agent: {response['message']}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            print("Session ended.")
            break

        response = agent.next(user_input)
        print(f"\nAgent: {response['message']}\n")


if __name__ == "__main__":
    main()
