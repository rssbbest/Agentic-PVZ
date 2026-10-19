"""Organiser-owned development gateway; provider keys never enter agents.

It serves the standard (OpenAI) chat completions API that the SDK's
`autopilot_sdk.llm.LLMClient` calls, by proxying to any OpenAI-compatible
endpoint (Amazon Bedrock at the event). A successful plain JSON strategy is cached in memory
(the most recent CACHE_SIZE answers). Tool calling is passed through:
`tools`, `tool_choice` and the other
standard options are forwarded, and assistant `tool_calls` (including any
provider extras such as Gemini thought signatures) are returned verbatim so
the agent can run its tools and send the results back.
"""
import hashlib
import hmac
import itertools
import json
import os
import time
from collections import OrderedDict, defaultdict, deque
import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title='Autopilot local LLM gateway')
failures = {}
stats = {'providerCalls': 0, 'cacheHits': 0, 'keyRetries': 0, 'lastStatus': 'not_called', 'model': None,
         'served': 0, 'refusedRate': 0, 'refusedToken': 0, 'upstreamErrors': 0}


def note(text: str) -> None:
    """One line in `docker compose logs gateway` for every refused or failed
    call (never tokens, keys or message contents), for monitoring."""
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)
rotation = itertools.count()
RETRY_NEXT_KEY = {401, 403, 429}


def access_tokens() -> set[str]:
    """AUTOPILOT_GATEWAY_TOKENS: comma-separated tokens agents must send as
    `Authorization: Bearer <token>`. Empty means open (local development)."""
    return {t.strip() for t in os.environ.get('AUTOPILOT_GATEWAY_TOKENS', '').split(',') if t.strip()}


def team_token(team: str, secret: str) -> str:
    """A team's own token, `<team>.<signature>`: with AUTOPILOT_GATEWAY_TEAM_SECRET
    set, the gateway accepts it without a list, and rate-limits each team apart."""
    team = team.strip()
    return f"{team}.{hmac.new(secret.encode(), team.encode(), hashlib.sha256).hexdigest()[:20]}"


def is_valid_token(token: str) -> bool:
    if token in access_tokens():
        return True
    secret = os.environ.get('AUTOPILOT_GATEWAY_TEAM_SECRET', '')
    team = token.rpartition('.')[0]
    return bool(secret and team) and hmac.compare_digest(token, team_token(team, secret))


RATE_PER_MIN = int(os.environ.get('AUTOPILOT_GATEWAY_RATE_PER_MIN', '60'))
MAX_OUTPUT_TOKENS = int(os.environ.get('AUTOPILOT_GATEWAY_MAX_TOKENS', '1024'))
MAX_REQUEST_CHARS = 200_000
recent = defaultdict(deque)   # token -> monotonic times of its recent calls


def admit(authorization: str) -> None:
    if not access_tokens() and not os.environ.get('AUTOPILOT_GATEWAY_TEAM_SECRET'):
        return
    token = authorization.removeprefix('Bearer ').strip()
    if not is_valid_token(token):
        stats['refusedToken'] += 1
        note('LLM refused 401: missing or wrong team token')
        raise HTTPException(401, 'Gateway token required (set AUTOPILOT_GATEWAY_KEY)')
    now, calls = time.monotonic(), recent[token]
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= RATE_PER_MIN:
        stats['refusedRate'] += 1
        note(f"LLM refused 429: team {token.rpartition('.')[0] or 'organiser'} over {RATE_PER_MIN} calls a minute")
        raise HTTPException(429, f'Gateway limit: {RATE_PER_MIN} LLM calls per minute per token')
    calls.append(now)


def provider_keys() -> list[str]:
    """AUTOPILOT_LLM_API_KEY holds one key or several separated by commas."""
    return [k.strip() for k in os.environ.get('AUTOPILOT_LLM_API_KEY', '').split(',') if k.strip()]
# In memory, bounded: during an event nearly every board is new, and a file
# re-read and re-written on every call grew with each one.
CACHE_SIZE = int(os.environ.get('AUTOPILOT_GATEWAY_CACHE_SIZE', '2000'))
cache: OrderedDict[str, dict] = OrderedDict()


# Request options forwarded to the provider unchanged.
FORWARDED = ('tools', 'tool_choice', 'parallel_tool_calls', 'response_format',
             'temperature', 'top_p', 'max_tokens', 'max_completion_tokens', 'stop', 'seed',
             'reasoning_effort')


class Chat(BaseModel):
    model_config = ConfigDict(extra='allow')
    messages: list[dict] = Field(min_length=1)
    model: str = os.environ.get('AUTOPILOT_LLM_MODEL', 'gpt-4o-mini')

    def options(self) -> dict:
        extra = self.model_extra or {}
        options = {k: extra[k] for k in FORWARDED if extra.get(k) is not None}
        # Fair play: every call is greedy (temperature 0), whatever the agent asks, so
        # the same board gets the same answer and a run's score is repeatable.
        options['temperature'] = 0
        options.pop('top_p', None)
        for key in ('max_tokens', 'max_completion_tokens'):
            if key in options:
                try:
                    options[key] = max(1, min(int(options[key]), MAX_OUTPUT_TOKENS))
                except (TypeError, ValueError):
                    options.pop(key)
        if 'max_tokens' not in options and 'max_completion_tokens' not in options:
            options['max_tokens'] = MAX_OUTPUT_TOKENS
        return options

    def provider_model(self) -> str:
        """The organiser's model, unless the request names one on the allow list."""
        default = os.environ.get('AUTOPILOT_LLM_MODEL', 'gpt-4o-mini')
        allowed = {m.strip() for m in os.environ.get('AUTOPILOT_LLM_ALLOWED_MODELS', '').split(',') if m.strip()}
        return self.model if self.model in allowed | {default} else default

    def uses_tools(self) -> bool:
        return bool((self.model_extra or {}).get('tools')) or any(
            m.get('role') == 'tool' or m.get('tool_calls') for m in self.messages)


@app.get('/health')
def health():
    return {'status': 'ok', 'gateway': 'local-development', 'cachedStrategies': len(cache),
            'tokenRequired': bool(access_tokens()),
            'keysConfigured': len(provider_keys()), **stats}


@app.post('/v1/chat/completions')
async def chat(body: Chat, authorization: str = Header(default='')):
    admit(authorization)
    if len(json.dumps(body.messages)) > MAX_REQUEST_CHARS:
        raise HTTPException(413, 'Request too large')
    model = body.provider_model()
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    if digest in cache:
        cache.move_to_end(digest)
        stats['cacheHits'] += 1
        stats['lastStatus'] = 'cached_success'
        stats['model'] = model
        return {**cache[digest], 'gateway': {'cached': True, 'provider': 'openai-compatible'}}
    failure = failures.get(digest)
    if failure and time.monotonic() - failure['time'] < 30:
        raise HTTPException(502, failure['message'])
    keys = provider_keys()
    base_url = (os.environ.get('AUTOPILOT_LLM_BASE_URL') or '').rstrip('/')
    if not keys or not base_url:
        note('LLM not configured: AUTOPILOT_LLM_BASE_URL / API key missing')
        raise HTTPException(503, 'Set AUTOPILOT_LLM_BASE_URL/API_KEY in .env and restart the stack')
    # No silent fallback: provider failure is visible to the participant.
    # Requests rotate across keys; a rate-limited or refused key passes the
    # request to the next one.
    start = next(rotation)
    async with httpx.AsyncClient(timeout=25) as client:
        stats['model'] = model
        for attempt in range(len(keys)):
            stats['providerCalls'] += 1
            try:
                response = await client.post(
                    f'{base_url}/chat/completions',
                    headers={'Authorization': f'Bearer {keys[(start + attempt) % len(keys)]}'},
                    json={'model': model, 'messages': body.messages, **body.options()})
            except httpx.HTTPError as exc:
                stats['lastStatus'] = 'connection_error'
                stats['upstreamErrors'] += 1
                note(f'LLM upstream error: {type(exc).__name__}')
                raise HTTPException(502, 'LLM connection failed')
            if response.status_code not in RETRY_NEXT_KEY:
                break
            stats['keyRetries'] += 1
    stats['lastStatus'] = str(response.status_code)
    if response.status_code != 200:
        # Do not log URLs, headers, keys or full provider error messages.
        message = f'LLM upstream returned HTTP {response.status_code}'
        stats['upstreamErrors'] += 1
        note(f'LLM upstream error: HTTP {response.status_code}')
        failures[digest] = {'time': time.monotonic(), 'message': message}
        raise HTTPException(502, message)
    data = response.json()
    try:
        choice = data['choices'][0]
        message = choice['message']
    except (KeyError, IndexError, TypeError):
        raise HTTPException(502, 'LLM upstream returned no message')
    stats['served'] += 1
    result = {'model': model,
              'choices': [{'index': 0, 'finish_reason': choice.get('finish_reason'),
                           'message': {'role': 'assistant', **message}}],
              'usage': data.get('usage', {})}
    if body.uses_tools():
        # Tool turns depend on live state; never cache them.
        return {**result, 'gateway': {'cached': False, 'provider': 'openai-compatible'}}
    try:
        json.loads(message['content'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, 'LLM upstream returned no valid JSON strategy')
    cache[digest] = result
    while len(cache) > CACHE_SIZE:
        cache.popitem(last=False)
    return {**result, 'gateway': {'cached': False, 'provider': 'openai-compatible'}}


if __name__ == '__main__':
    # python -m integration.gateway --team-tokens 0004 0005 ...: print each team's token.
    import sys
    secret = os.environ.get('AUTOPILOT_GATEWAY_TEAM_SECRET', '')
    if sys.argv[1:2] != ['--team-tokens'] or not secret:
        sys.exit('usage: AUTOPILOT_GATEWAY_TEAM_SECRET=... python -m integration.gateway --team-tokens TEAM...')
    for team in sys.argv[2:]:
        print(f'{team}  {team_token(team, secret)}')
