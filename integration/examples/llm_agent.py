"""Uses the organiser's local gateway, never a direct provider or provider key.
The same general-purpose strategy is cached by the gateway for all waves.
"""
import json
import os
from autopilot_sdk import Agent
from autopilot_sdk.llm import LLMClient


class LLMAgent(Agent):
    def place_defences(self, enemies):
        client = LLMClient(api_key='', timeout=30,
            base_url=os.environ.get('AUTOPILOT_GATEWAY_URL', 'http://127.0.0.1:8004/v1'))
        response = client.chat.completions.create(messages=[{'role': 'user', 'content': (
            'Create a strong general-purpose zombie defence layout. Return only a JSON object '
            'with defencePlacementData array. Exactly 25 unique cells: x,y integers 0..4. '
            'Enemies enter from x=5 and travel LEFT along their lane y; protect all 5 lanes. '
            'defenceId 0=penguin (200HP,500 collision damage, slides RIGHT after being hit), '
            '1=soldier (200HP,20 damage, lane shooter), 2=archer (200HP,10 damage, any lane), '
            '3=shield (600HP,no attack). '
            'Use mixed defences and put shields toward the enemy. '
            'Each item: {"defenceId":0,"defenceGridPosition":{"x":0,"y":0}}. '
            'All cells must be populated exactly once; no other properties.'
        )}], model=os.environ.get('AUTOPILOT_LLM_MODEL', 'gpt-4o-mini'))
        data = json.loads(response['choices'][0]['message']['content'])
        # Accept a valid placement array as well as the requested wrapper.
        return data['defencePlacementData'] if isinstance(data, dict) else data
