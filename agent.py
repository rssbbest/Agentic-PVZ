"""Meeting demo: ask the organiser gateway for a formation, then return it.

Run: autopilot verify --agent agent.py
Submit: autopilot submit --agent agent.py --team-id YOUR_TEAM_ID
Provider credentials belong to the gateway, not this file.
"""
import json
import os

from autopilot_sdk import Agent
from autopilot_sdk.llm import LLMClient


class DemoAgent(Agent):
    def place_defences(self, enemies):
        client = LLMClient(
            api_key='', timeout=30,
            base_url=os.environ.get('AUTOPILOT_GATEWAY_URL', 'http://127.0.0.1:8004/v1'),
        )
        response = client.chat.completions.create(
            model=os.environ.get('AUTOPILOT_LLM_MODEL', 'gpt-4o-mini'),
            messages=[{'role': 'user', 'content': (
                'Autopilot Unity build4 integration demo, version 2026-10-08. '
                'Create one general-purpose defence formation for all enemy waves. '
                'Reply only with a JSON object containing defencePlacementData. '
                'Fill exactly 25 unique cells, x and y integers from 0 to 4. '
                'Enemies enter from the RIGHT, x=5, and move LEFT in each lane y. '
                'Defence IDs: 0=penguin, 200 HP, 500 collision damage, slides right; '
                '1=soldier, 200 HP, 20 damage, lane shooter; '
                '2=archer, 200 HP, 10 damage, targets any lane; '
                '3=shield, 600 HP, no attack. Use ranged units behind shields. '
                'Every entry must have only defenceId and defenceGridPosition. '
                'Example entry: {"defenceId":1,"defenceGridPosition":{"x":0,"y":0}}.'
            )}],
        )
        strategy = json.loads(response['choices'][0]['message']['content'])
        return strategy['defencePlacementData'] if isinstance(strategy, dict) else strategy
