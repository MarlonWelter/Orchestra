# Orchestra

Orchestra is a general-purpose engine for coordinating multiple AI agents in a structured, turn-based workflow.

The core idea is simple: each agent receives a task, responds from its assigned role, and then explicitly forwards the conversation to the next agent. This makes it possible to model collaborative reasoning processes where different agents contribute specialized perspectives, challenge each other, and gradually build toward a final result.

## Concept

An Orchestra input consists of three parts:

1. **System prompt** – defines the general rules of the agent system and the handoff protocol.
2. **Role description** – defines the current agent's identity, responsibilities, character, beliefs, skills, and relationships to other agents.
3. **Current input** – contains the actual request, the sending agent, relevant context, and the task for the current agent.

Each turn produces:

- the current agent's response,
- the selected next recipient,
- and a concrete task for that recipient.

## Goal

The project aims to provide a reusable foundation for building agent teams that can reason, delegate, critique, and synthesize results in a transparent and controllable way.
