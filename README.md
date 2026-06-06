# Orchestra

Orchestra is a general-purpose engine for coordinating multiple AI agents in a structured, turn-based workflow.

The core idea is to model an agent team as a set of explicitly defined roles. Each agent receives an input, responds from its assigned role, and then forwards the conversation to exactly one next agent. This creates a controlled form of multi-agent collaboration where agents can delegate, critique, discuss, and synthesize results while the engine keeps the process sequential and inspectable.

## Core idea

A user first creates an agent team. Each agent is defined by its role prompt, its responsibilities, its character, its assumptions or beliefs, its available context, and its relationship to the other agents in the team.

One agent is configured as the entry agent. External requests are sent to this agent first. In many teams this will be a manager-like agent that receives the user's request, decides which specialist should be involved, and eventually returns the final answer to the outside world.

From there, the workflow proceeds like a turn-based game:

1. The engine calls the currently active agent.
2. The agent processes the current input from its own role.
3. The agent returns a response and selects the next agent.
4. The engine validates the handoff and calls the next agent.
5. The process continues until the responsible agent returns the final result.

Only one agent has the ball at a time. Agents can still have discussions, challenge each other, and refer to previous turns, but technically the process remains sequential.

## Agent input structure

Each agent call consists of three parts:

1. **System prompt** – defines the general rules of the agent system and the handoff protocol.
2. **Role description** – defines the current agent's identity, responsibilities, character, beliefs, skills, context, tools, and relationships to other agents.
3. **Current input** – contains the actual request, the sending agent, the relevant conversation history, and the concrete task for the current agent.

Each turn produces:

- the current agent's response,
- the selected next recipient,
- and a concrete task for that recipient.

## Engine responsibilities

The engine is responsible for running the agent network. It does not have to understand the full domain logic itself, but it must control the execution flow.

Core responsibilities:

- store the team configuration,
- know which agent receives external input first,
- build the prompt for each agent call,
- invoke the configured language model for the active agent,
- parse the agent's handoff response,
- select and call the next agent,
- keep track of the conversation state,
- enforce limits such as energy, turns, or token budgets,
- and stop the process when a final answer is returned.

## Agents and models

Behind each agent is a large language model. Initially, all agents may use the same model. Later, different agents could use different model providers or model types. For example, one agent might use ChatGPT, another Claude, another Grok, and another a smaller local model.

This means the agent identity should be separate from the model implementation. An agent is defined by its role, context, tools, and behavior. The model is only the execution backend used for that agent call.

## Agent context and tools

Agents should not have to start from zero in every run. Each agent may have access to a dedicated context area, such as files, notes, memory, or a folder-like knowledge base. Agents may also be allowed to update parts of their own context over time.

In later versions, agents may also use tools. Examples:

- searching the web,
- scanning news sources,
- reading internal documents,
- updating their own notes,
- calling APIs,
- or triggering other external workflows.

The first version can start with manual user input only. Automatic inputs, such as a news-scanning agent that creates new tasks when relevant events occur, can be added later.

## Energy budget

Orchestra uses an energy budget to prevent agent workflows from running indefinitely and consuming unbounded resources.

A request starts with a configurable amount of energy, for example `100`. In the simplest version, each language model call costs `1` energy. Every turn reduces the remaining energy. When energy becomes low, agents should avoid unnecessary delegation and move toward a result.

This is intentionally a simple first abstraction. Later versions may calculate cost more accurately based on tokens, model prices, tool calls, execution time, or task complexity.

## Example use case

One possible use case is an investment analysis team.

A user could define:

- a manager agent that receives external requests,
- a conservative value-investing agent,
- a technology and disruption agent,
- a risk-focused reality-check agent,
- and specialist agents responsible for individual companies or sectors.

The user might then ask the team to analyze the current world situation and propose a portfolio. The manager receives the request first, asks selected agents for their perspectives, routes the discussion through the team, and finally returns a synthesized result.

This is only one possible use case. Orchestra itself is intended to be domain-neutral.

## Goal

The project aims to provide a reusable foundation for building agent teams that can reason, delegate, critique, discuss, and synthesize results in a transparent and controllable way.
