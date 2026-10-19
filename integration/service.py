"""Real HTTP integration: simulator, evaluator, browser UI, optional Unity queue."""
import asyncio
import json
import os
import time
import uuid
from collections import deque
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from autopilot_sdk.contracts.schemas import DefencePlacement, EnemyEntry
from .combat import Battle
from .engine_data import ROOT, catalog

BACKEND = os.environ.get('AUTOPILOT_BACKEND_URL', 'http://127.0.0.1:8000')
SECRET = os.environ['SIMULATOR_SECRET']
MODE = os.environ.get('SIMULATOR_MODE', 'portable')
app = FastAPI(title='Autopilot simulator + evaluator + play')
app.add_middleware(CORSMiddleware, allow_origins=[
    'http://localhost:8002', 'http://127.0.0.1:8002',
    'http://localhost:8005', 'http://127.0.0.1:8005'],
    allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
app.mount('/static', StaticFiles(directory=ROOT / 'integration/static'), name='static')
app.mount('/engine', StaticFiles(directory=ROOT / '.runtime/managed-webgl',
          html=True, check_dir=False), name='unity-engine')
app.mount('/original', StaticFiles(directory=ROOT / '.runtime/latest-webgl/build4',
          html=True, check_dir=False), name='original-build4')
jobs = {}
unity_queue = deque()
work = set()
slots = asyncio.Semaphore(4)
unity_seen = 0.
unity_web_seen = 0.
webgl_active = {}
webgl_lock = asyncio.Lock()


class Start(BaseModel):
    runId: int = Field(gt=0)
    enemies: list[EnemyEntry]
    defencePlacementData: list[DefencePlacement] = Field(min_length=1, max_length=25)


class Practice(BaseModel):
    enemies: list[EnemyEntry]
    defencePlacementData: list[DefencePlacement] = Field(min_length=1, max_length=25)
    timeScale: float = Field(default=2, ge=1, le=20)


class Evaluate(BaseModel):
    result: dict


class Frame(BaseModel):
    snapshot: dict


class TeamRequest(BaseModel):
    teamID: str = Field(min_length=1, max_length=80)


class LegacyResult(BaseModel):
    teamID: str
    durationSeconds: float = Field(ge=0, allow_inf_nan=False)
    enemiesKilled: int = Field(ge=0)
    defencesDestroyed: int = Field(ge=0)
    defences: list[dict] = Field(default_factory=list)
    enemies: list[dict] = Field(default_factory=list)
    success: bool = False


def validate_unique(placement):
    cells = [(p.defenceGridPosition.x, p.defenceGridPosition.y) for p in placement]
    if len(cells) != len(set(cells)):
        raise HTTPException(422, 'Each grid cell holds at most one defence')


def spawn(coro):
    task = asyncio.create_task(coro)
    work.add(task)
    task.add_done_callback(work.discard)


@app.get('/')
def index():
    return FileResponse(ROOT / 'integration/static/index.html')


@app.get('/health')
def health():
    return {'status': 'ok', 'engine': MODE, 'jobs': len(jobs),
            'unityConnected': time.monotonic() - unity_seen < 10,
            'unityWebConnected': time.monotonic() - unity_web_seen < 10,
            'webBuildReady': (ROOT / '.runtime/managed-webgl/index.html').exists()}


@app.get('/catalog')
def game_catalog():
    return catalog()


@app.get('/examples/{kind}')
def example(kind: str):
    if kind not in ('test', 'llm', 'demo'):
        raise HTTPException(404)
    path = ROOT / 'agent.py' if kind == 'demo' else ROOT / f'integration/examples/{kind}_agent.py'
    return FileResponse(path,
                        filename=f'{kind}_agent.py', media_type='text/x-python')


@app.get('/api/{path:path}')
async def read_backend(path: str):
    if not (path in ('health', 'enemy_configs', 'leaderboard')
            or path.startswith(('submissions/', 'runs/'))):
        raise HTTPException(404)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f'{BACKEND}/{path}')
    if response.status_code != 200:
        raise HTTPException(response.status_code, 'Backend request failed')
    return response.json()


@app.get('/gateway/status')
async def gateway_status():
    async with httpx.AsyncClient(timeout=3) as client:
        r = await client.get('http://127.0.0.1:8004/health')
    return r.json()


@app.post('/demo/{kind}')
async def demo(kind: str):
    if kind not in ('test', 'llm'):
        raise HTTPException(404)
    async with httpx.AsyncClient(timeout=30) as client:
        team = await client.post(f'{BACKEND}/teams', json={'name': f'{kind}-demo-{uuid.uuid4().hex[:8]}'})
        team.raise_for_status()
        path = ROOT / 'agent.py' if kind == 'llm' else ROOT / f'integration/examples/{kind}_agent.py'
        response = await client.post(f'{BACKEND}/submissions',
            data={'team_id': str(team.json()['id'])},
            files={'agent_file': (path.name, path.read_bytes(), 'text/x-python')})
        response.raise_for_status()
    return response.json()


@app.post('/submit', status_code=201)
async def upload_agent(agent_file: UploadFile, team_name: str = Form(...)):
    if not team_name.strip() or len(team_name) > 80:
        raise HTTPException(422, 'Provide a team name of 1 to 80 characters')
    source = await agent_file.read(256 * 1024 + 1)
    if len(source) > 256 * 1024:
        raise HTTPException(413, 'Agent must be at most 256 KiB')
    from autopilot_sdk.verifier.static_scan import scan_source
    try:
        report = scan_source(source.decode('utf-8'))
    except UnicodeDecodeError:
        raise HTTPException(422, 'Agent must be UTF-8 Python')
    if not report.passed:
        raise HTTPException(422, '; '.join(report.violations))
    async with httpx.AsyncClient(timeout=30) as client:
        team = await client.post(f'{BACKEND}/teams', json={'name': team_name.strip()})
        if team.status_code == 409:
            raise HTTPException(409, 'Team name exists; choose a new name or submit with the CLI and your team ID')
        team.raise_for_status()
        response = await client.post(f'{BACKEND}/submissions',
            data={'team_id': str(team.json()['id'])},
            files={'agent_file': ('agent.py', source, 'text/x-python')})
        response.raise_for_status()
    return response.json()


@app.post('/start', status_code=202)
async def start(body: Start):
    validate_unique(body.defencePlacementData)
    key = str(body.runId)
    existing = jobs.get(key)
    if existing and existing['status'] not in ('callback_failed', 'failed'):
        return {'accepted': True, 'runId': body.runId}
    jobs[key] = {'status': 'queued', 'request': body.model_dump(), 'engine': MODE}
    if MODE == 'unity':
        unity_queue.append(key)
        spawn(unity_timeout(key, jobs[key]))
    else:
        spawn(simulate(key, body.model_dump(), callback=True))
    return {'accepted': True, 'runId': body.runId}


@app.post('/api/team')
async def webgl_team(body: TeamRequest):
    """Adapt the supplied build's team-ID request to a real queued backend run."""
    try:
        team_id = int(body.teamID)
    except ValueError:
        raise HTTPException(404, 'Enter the numeric team ID returned by registration')
    async with webgl_lock:
        previous = webgl_active.get(team_id)
        if previous and jobs[previous]['status'] == 'running':
            raise HTTPException(409, 'This team already has an active game')
        async with httpx.AsyncClient(timeout=10) as client:
            for key in list(unity_queue):
                if jobs[key]['status'] != 'queued':
                    continue
                run = await client.get(f'{BACKEND}/runs/{key}')
                run.raise_for_status()
                submission = await client.get(f'{BACKEND}/submissions/{run.json()["submission_id"]}')
                submission.raise_for_status()
                if submission.json()['team_id'] == team_id:
                    unity_queue.remove(key)
                    jobs[key]['status'] = 'running'
                    webgl_active[team_id] = key
                    return jobs[key]['request']
    raise HTTPException(404, 'No queued run for this team. Submit agent.py first.')


@app.post('/api/results', status_code=202)
async def webgl_results(body: LegacyResult):
    """Keep the original client report intact; expose its legacy completion semantics."""
    try:
        team_id = int(body.teamID)
    except ValueError:
        raise HTTPException(404, 'Unknown team')
    key = webgl_active.get(team_id)
    if key is None:
        raise HTTPException(409, 'No active WebGL game for this team')
    if jobs[key]['status'] in ('evaluating', 'completed'):
        if jobs[key].get('legacyReport') == body.model_dump():
            return {'accepted': True, 'runId': int(key)}
        raise HTTPException(409, 'This game already reported a result')
    if jobs[key]['status'] != 'running':
        raise HTTPException(409, 'No active WebGL game')
    request = jobs[key]['request']
    total = sum(e['count'] for e in request['enemies'])
    placed = len(request['defencePlacementData'])
    if body.enemiesKilled > total or body.defencesDestroyed > placed:
        raise HTTPException(422, 'Client counters exceed the assigned wave or formation')
    result = {
        'engine': 'unity-webgl-build4', 'totalEnemies': total,
        'killed': body.enemiesKilled, 'escaped': total - body.enemiesKilled,
        'defencesPlaced': placed, 'defencesAlive': placed - body.defencesDestroyed,
        'durationSeconds': body.durationSeconds, 'won': body.enemiesKilled == total,
        'termination': 'legacy-client-report; unresolved enemies counted as breaches',
        'legacyReport': body.model_dump(),
    }
    jobs[key]['legacyReport'] = body.model_dump()
    jobs[key]['result'] = result
    jobs[key]['status'] = 'evaluating'
    spawn(finish_unity(key, result))
    return {'accepted': True, 'runId': int(key)}


@app.post('/practice', status_code=202)
async def practice(body: Practice):
    validate_unique(body.defencePlacementData)
    key = 'practice-' + uuid.uuid4().hex[:8]
    jobs[key] = {'status': 'queued', 'request': body.model_dump(), 'engine': 'portable'}
    spawn(simulate(key, body.model_dump(), callback=False, speed=1))
    return {'runId': key}


@app.post('/unity/practice', status_code=202)
async def unity_practice(body: Practice):
    validate_unique(body.defencePlacementData)
    if time.monotonic() - max(unity_seen, unity_web_seen) >= 10:
        raise HTTPException(503, 'Open the Unity player before starting a Unity practice battle')
    run_id = 1_000_000_000 + (uuid.uuid4().int % 1_000_000_000)
    key = str(run_id)
    payload = {**body.model_dump(), 'runId': run_id}
    jobs[key] = {'status': 'queued', 'request': payload, 'engine': 'unity', 'callback': False,
                 'worker': 'web' if time.monotonic() - unity_web_seen < 10 else 'any'}
    unity_queue.appendleft(key)
    spawn(unity_timeout(key, jobs[key]))
    return {'runId': key}


async def send_result(key, result):
    last = None
    async with httpx.AsyncClient(timeout=65) as client:
        for attempt in range(4):
            try:
                response = await client.post(f'{BACKEND}/runs/{key}/result',
                    headers={'X-Simulator-Secret': SECRET}, json={'result': result})
                if response.status_code == 409:
                    current = await client.get(f'{BACKEND}/runs/{key}')
                    if current.status_code == 200 and current.json()['status'] == 'completed':
                        jobs[key]['status'] = 'completed'
                        return
                response.raise_for_status()
                jobs[key]['status'] = 'completed'
                return
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                await asyncio.sleep(.5 * (attempt + 1))
    jobs[key]['status'] = 'callback_failed'
    jobs[key]['error'] = last


async def simulate(key, payload, callback, speed=10):
    async with slots:
        battle = Battle(payload['enemies'], payload['defencePlacementData'], seed=42)
        jobs[key]['status'] = 'running'
        frames = []
        while not battle.done:
            for _ in range(4):
                battle.step()
            frame = json.loads(json.dumps(battle.snapshot()))
            jobs[key]['snapshot'] = frame
            frames.append(frame)
            await asyncio.sleep(.2 / speed)
        result = battle.result()
        jobs[key]['result'] = result
        jobs[key]['frames'] = frames
        if callback:
            jobs[key]['status'] = 'evaluating'
            await send_result(key, result)
        else:
            jobs[key]['status'] = 'completed'
        # Keep inspectable results/replays across local restarts.
        out = ROOT / '.runtime/replays'
        out.mkdir(parents=True, exist_ok=True)
        (out / f'{key}.json').write_text(json.dumps(jobs[key]))


def load_job(key: str):
    if key in jobs:
        return jobs[key]
    path = ROOT / '.runtime/replays' / f'{key}.json'
    if '/' in key or '..' in key or not path.exists():
        raise HTTPException(404, 'Battle not available')
    jobs[key] = json.loads(path.read_text())
    return jobs[key]


@app.get('/battles/{key}')
def battle(key: str):
    return {k: v for k, v in load_job(key).items() if k != 'frames'}


@app.get('/battles/{key}/replay')
def replay(key: str):
    return load_job(key).get('frames', [])


@app.websocket('/watch/{key}')
async def watch(ws: WebSocket, key: str):
    await ws.accept()
    try:
        while True:
            try:
                job = load_job(key)
            except HTTPException:
                job = None
            if not job:
                await ws.send_json({'status': 'waiting'})
            else:
                await ws.send_json({k: v for k, v in job.items() if k != 'frames'})
                if job['status'] in ('completed', 'callback_failed', 'failed'):
                    break
            await asyncio.sleep(.1)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.post('/evaluate')
def evaluate(body: Evaluate):
    r = body.result
    required = ('totalEnemies', 'killed', 'escaped', 'defencesPlaced', 'defencesAlive')
    if any(type(r.get(k)) is not int or r[k] < 0 for k in required):
        raise HTTPException(422, 'Result requires non-negative integer combat counters')
    if r['killed'] + r['escaped'] != r['totalEnemies'] or r['defencesAlive'] > r['defencesPlaced']:
        raise HTTPException(422, 'Inconsistent combat counters')
    kill = r['killed'] / r['totalEnemies'] if r['totalEnemies'] else 1
    survival = r['defencesAlive'] / r['defencesPlaced'] if r['defencesPlaced'] else 0
    # Development score, documented and deterministic; event organisers may replace it.
    score = round(80 * kill + 20 * survival, 4)
    return {'score': score, 'breakdown': {'killRatio': kill, 'survivalRatio': survival},
            'formula': '80 * killRatio + 20 * survivalRatio', 'result': r}


@app.get('/unity/jobs/next')
def unity_next(worker: str = 'native'):
    global unity_seen, unity_web_seen
    if worker not in ('native', 'web'):
        raise HTTPException(422, 'Unknown Unity worker')
    if worker == 'web':
        unity_web_seen = time.monotonic()
    else:
        unity_seen = time.monotonic()
    for key in list(unity_queue):
        if jobs[key]['status'] != 'queued':
            unity_queue.remove(key)
            continue
        if jobs[key].get('worker', 'any') not in ('any', worker):
            continue
        unity_queue.remove(key)
        jobs[key]['status'] = 'running'
        jobs[key]['claimedBy'] = worker
        return jobs[key]['request']
    return {'runId': 0, 'enemies': [], 'defencePlacementData': []}


@app.post('/unity/jobs/{key}/frame', status_code=202)
def unity_frame(key: str, body: Frame):
    global unity_seen, unity_web_seen
    if key not in jobs or jobs[key]['status'] != 'running':
        raise HTTPException(409, 'No active Unity job')
    if jobs[key].get('claimedBy') == 'web':
        unity_web_seen = time.monotonic()
    else:
        unity_seen = time.monotonic()
    jobs[key]['snapshot'] = body.snapshot
    jobs[key].setdefault('frames', []).append(body.snapshot)
    return {'accepted': True}


@app.post('/unity/jobs/{key}/result', status_code=202)
async def unity_result(key: str, body: Evaluate):
    global unity_seen, unity_web_seen
    if key in jobs and jobs[key]['status'] in ('evaluating', 'completed'):
        if jobs[key].get('result') == body.result:
            return {'accepted': True}
    if key not in jobs or jobs[key]['status'] != 'running':
        raise HTTPException(409, 'No active Unity job')
    evaluate(body)
    if jobs[key].get('claimedBy') == 'web':
        unity_web_seen = time.monotonic()
    else:
        unity_seen = time.monotonic()
    request = jobs[key]['request']
    if (body.result['totalEnemies'] != sum(e['count'] for e in request['enemies'])
            or body.result['defencesPlaced'] != len(request['defencePlacementData'])):
        raise HTTPException(422, 'Result counters do not match the assigned game')
    jobs[key]['result'] = body.result
    if 'snapshot' in jobs[key]:
        jobs[key]['snapshot']['done'] = True
    jobs[key]['status'] = 'evaluating'
    spawn(finish_unity(key, body.result))
    return {'accepted': True}


async def finish_unity(key, result):
    if jobs[key].get('callback', True):
        await send_result(key, result)
    else:
        jobs[key]['status'] = 'completed'
    out = ROOT / '.runtime/replays'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{key}.json').write_text(json.dumps(jobs[key]))


async def unity_timeout(key, job):
    await asyncio.sleep(1800)
    if jobs.get(key) is job and job['status'] in ('queued', 'running'):
        job['status'] = 'failed'
        job['error'] = 'Unity player did not finish within 1800 seconds'
        # Backend watchdog handles infrastructure failures (never a fabricated score).
