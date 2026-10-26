"""Foreground, token-free Herdr transport for quick milestone events."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .common import DELIVERY_LEASE_SECONDS, die, now_s, stamp
from .paths import read_dispatch, root
from .publication import fault, publish_json
from .policy import allowed_models_for_role, assignment_role, is_quick_milestone, need_run, plan_flag
from .assignments import config_paths, read_config
from .locks import owner_lock, owner_lock_held
from .state import batch_path, list_batches, list_incidents, read_batch, reopen_epoch, state_of
from .events import derive_events
from .delivery import (
    IDENTITY_FIELDS, announce_active, announce_delivered, announce_path,
    delivery_lock, delivery_log, event_actionable, event_is_owned, paused_flag,
    inbox_retry, read_announce, read_pending, received_before, remember_receipt, sweep_role,
)
from .liveness import read_registration
from .sessions import run_harness_sessions
from .signalling import ledger_for
from .health import flag_stall
from .prompts import compose_prompt
from .dispatch import guard_review_reserve
from .batches import cmd_batch_verify, quick_milestone_implementation_problem, quick_milestone_plan_problems
from .events import codex_budget_events


class HerdrError(Exception):
    def __init__(self, message: str, code: str = 'transport'):
        super().__init__(message)
        self.code = code


def herdr(*args: str, raw: bool = False) -> dict | str:
    try:
        result = subprocess.run(['herdr', *args], capture_output=True, text=True,
                                cwd=root(), timeout=40)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HerdrError(str(exc)) from exc
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f'herdr exited {result.returncode}'
        try:
            code = json.loads(message)['error']['code']
        except (ValueError, KeyError, TypeError):
            code = 'transport'
        raise HerdrError(message, code)
    if raw:
        return result.stdout
    try:
        return json.loads(result.stdout)['result']
    except (ValueError, KeyError, TypeError) as exc:
        raise HerdrError('herdr returned an invalid JSON result') from exc


def conductor_dir(d: Path) -> Path:
    return d / '.conductor'


def worker_path(d: Path, bid: str, role: str) -> Path:
    return conductor_dir(d) / f'{bid}-{role}.json'


def worker_records(d: Path) -> list[dict]:
    return [read_worker(path) for path in sorted(conductor_dir(d).glob('*-*.json'))]


def worker_pane(d: Path, role: str, session: str) -> str:
    path = conductor_dir(d) / 'layout.json'
    layout = read_worker(path)
    if not layout.get('tab_id'):
        label = 'docket-' + hashlib.sha256(str(d.resolve()).encode()).hexdigest()[:12]
        tabs = herdr('tab', 'list')['tabs']
        existing = next((tab for tab in tabs if tab.get('label') == label), {})
        if existing:
            panes = herdr('pane', 'list')['panes']
            anchor = next((p['pane_id'] for p in panes if p.get('tab_id') == existing['tab_id']), '')
            if not anchor:
                raise HerdrError('recorded worker tab has no anchor pane')
            layout = {'tab_id': existing['tab_id'], 'anchor_pane_id': anchor}
        else:
            tab = herdr('tab', 'create', '--cwd', str(root()), '--label', label, '--no-focus')
            layout = {'tab_id': tab['tab']['tab_id'], 'anchor_pane_id': tab['root_pane']['pane_id']}
        publish_json(path, layout)
    pane = herdr('pane', 'split', '--pane', layout['anchor_pane_id'], '--direction', 'down',
                 '--cwd', str(root()), '--no-focus', '--env', f'DOCKET_RUN={d.name}',
                 '--env', f'DOCKET_ROLE={role}', '--env', f'DOCKET_SESSION={session}')
    return pane['pane']['pane_id']


def read_worker(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        die(f'cannot read conductor record {path}: {exc}')
    if not isinstance(data, dict):
        die(f'invalid conductor record {path}: expected an object')
    return data


def conductor_status(d: Path) -> list[str]:
    if not (conductor_dir(d) / 'managed.json').is_file():
        return []
    lines = ['conductor: ' + ('running' if owner_lock_held(d, 'conductor') else 'not running')]
    layout = read_worker(conductor_dir(d) / 'layout.json')
    if layout:
        lines.append(f"  worker tab: {layout['tab_id']}")
    for path in sorted(conductor_dir(d).glob('*-*.json')):
        record = read_worker(path)
        lines.append(f"  {record['milestone']} {record['role']}: pane={record.get('pane_id') or 'pending'} "
                     f"agent={record['agent_name']}" + (' closed' if record.get('closed_at') else '') +
                     f"; last delivery: {record.get('last_delivery_at') or 'none'} "
                     f"{record.get('last_delivery_key', '')}")
    return lines


@contextlib.contextmanager
def conductor_lock(d: Path):
    path = d / '.locks/conductor.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die(f'conductor for {d.name} is already running')
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def launch_args(harness: str, model: str) -> list[str]:
    user_path, _project_path = config_paths()
    config = read_config(user_path, user=True)
    args = list(config.get(f'{harness}_launch_args', []))
    if model:
        args.extend(['-m' if harness == 'codex' else '--model', model])
    return args


def agent_name(d: Path, bid: str, role: str) -> str:
    address = hashlib.sha256(f'{d.resolve()}:{bid}:{role}'.encode()).hexdigest()[:16]
    return f'docket-{address}-' + ('impl' if role == 'implementor' else 'review')


def matching_agent(record: dict, agents: list[dict]) -> dict:
    agent = next((a for a in agents if a.get('name') == record.get('agent_name')), {})
    if agent and record.get('pane_id') and agent.get('pane_id') != record['pane_id']:
        die(f"conductor agent {record['agent_name']} moved or was replaced; inspect its pane before recovery")
    kind = agent.get('agent') or agent.get('kind')
    if kind and kind != record['requested_harness']:
        die(f"conductor agent {record['agent_name']} has a different live harness; inspect before recovery")
    native = agent.get('agent_session')
    if native and record.get('native_session_id') and native != record['native_session_id']:
        die(f"conductor agent {record['agent_name']} has a different native session; inspect before recovery")
    return agent


def bind_worker_registration(d: Path, record: dict) -> dict:
    reg = read_registration(record['session'])
    if not reg:
        return {}
    if (reg.get('run') != d.name or reg.get('role') != record['role']
            or reg.get('workspace') != str(root().resolve())):
        die(f"conductor recipient {record['session']} is no longer registered here")
    generation = record.get('session_generation')
    if generation is not None and generation != reg.get('generation'):
        die(f"conductor recipient {record['session']} changed generation; recover its recorded session "
            "instead of inheriting the old worker's deliveries")
    native = reg.get('native_harness') or {}
    identity = {'session_generation': reg['generation'],
                'native_session_id': native.get('session_id') or record.get('native_session_id', ''),
                'native_harness': native.get('harness') or record.get('native_harness', '')}
    if any(record.get(k) != v for k, v in identity.items()):
        record.update(identity)
        publish_json(worker_path(d, record['milestone'], record['role']), record)
    return reg


def previous_reviewer(d: Path, batch: dict) -> dict:
    milestones = sorted([b for b in list_batches(d) if b.get('milestone')],
                        key=lambda b: milestone_sort_key(str(b['batch'])))
    index = next((i for i, b in enumerate(milestones) if b['batch'] == batch['batch']), 0)
    if not index or milestones[index - 1].get('reviewer_context') == 'fresh':
        return {}
    return read_worker(worker_path(d, str(milestones[index - 1]['batch']), 'reviewer'))


def ensure_worker(d: Path, batch: dict, role: str, agents: list[dict]) -> dict:
    bid = str(batch['batch'])
    path = worker_path(d, bid, role)
    record = read_worker(path)
    if not record:
        assignment = assignment_role(batch, role)
        if not assignment:
            die(f'milestone {bid} has no recorded {role} assignment')
        refusal = io.StringIO()
        try:
            with contextlib.redirect_stderr(refusal):
                guard_review_reserve(d, str(assignment['model']))
        except SystemExit:
            conductor_stall(d, {}, f'premium launch refused for {bid}: ' + refusal.getvalue().strip())
            return {}
        if role == 'reviewer' and batch.get('reviewer_context') == 'continue':
            candidate = previous_reviewer(d, batch)
            if candidate and not candidate.get('closed_at') and matching_agent(candidate, agents):
                if (candidate['requested_harness'], candidate['requested_model']) != (
                        assignment['harness'], assignment['model']):
                    die(f'milestone {bid} asks to continue a reviewer with a different assignment; '
                        'record reviewer_context fresh with a reason before launching it')
                record = {**candidate, 'milestone': bid}
                publish_json(path, record)
        if not record:
            name = agent_name(d, bid, role)
            record = {'milestone': bid, 'role': role, 'agent_name': name,
                      'session': name, 'requested_harness': assignment['harness'],
                      'requested_model': assignment['model'], 'pane_id': '',
                      'launched_at': stamp(), 'started_at': '', 'closed_at': '',
                      'last_delivery_at': '', 'last_delivery_key': ''}
            publish_json(path, record)
    if record.get('closed_at'):
        return record
    bind_worker_registration(d, record)
    agent = matching_agent(record, agents)
    if agent:
        if not record.get('pane_id') or not record.get('started_at'):
            record.update(pane_id=agent['pane_id'], started_at=stamp())
            publish_json(path, record)
        return record
    # A vanished registered worker needs its native session resumed, never a fresh writer.
    if record.get('started_at') or read_registration(record['session']):
        return record
    if not record.get('pane_id'):
        record['pane_id'] = worker_pane(d, role, record['session'])
        publish_json(path, record)
    start_worker(d, record)
    return record


def start_worker(d: Path, record: dict) -> None:
    path = worker_path(d, record['milestone'], record['role'])
    args = launch_args(record['requested_harness'], record['requested_model'])
    native = record.get('resume_native_id')
    if native:
        if record['requested_harness'] == 'claude':
            args += ['--resume', native]
        elif record['requested_harness'] == 'codex':
            args = ['resume', native, *args]
        else:
            args += ['--session', native]
    try:
        herdr('agent', 'start', record['agent_name'], '--kind', record['requested_harness'],
              '--pane', record['pane_id'], '--', *args)
    except HerdrError as exc:
        # Startup may leave a named agent at repository trust or a permission dialog.
        record['launch_error'] = str(exc)
        publish_json(path, record)
        if exc.code != 'agent_not_ready':
            raise
        return
    fault('conduct:started')
    record['started_at'] = stamp()
    record.pop('launch_error', None)
    publish_json(path, record)


def idle_worker(record: dict) -> bool:
    if record.get('closed_at'):
        return False
    try:
        agent = herdr('agent', 'get', record['agent_name'])['agent']
        if not matching_agent(record, [agent]):
            return False
        return agent.get('agent_status') in ('idle', 'done')
    except HerdrError as exc:
        if exc.code == 'agent_not_found':
            return False
        raise


def conductor_prompt_sections(prompt: str) -> dict[str, str]:
    result = {}
    for chunk in re.split(r"(?m)(?=^# )", prompt):
        if not chunk.strip():
            continue
        name = chunk.splitlines()[0]
        if name == '# Steps':
            for step in re.split(r"(?m)(?=^[0-9]+\. )", chunk)[1:]:
                result[f"# Steps / {step.split('.', 1)[0]}"] = step.strip()
        else:
            result[name] = chunk.strip()
    return result


def worker_prompt(d: Path, batch: dict, record: dict, event: dict,
                  section_hashes: dict | None = None) -> str:
    role, bid, sid = record['role'], str(batch['batch']), record['session']
    owner = str(event.get('owner') or '')
    header = (f"You are the {role} for Docket run {d.name}, milestone {bid}, in a conductor-managed "
              f"session. Your transport session is {sid}. "
              "Worker wakes come only from the conductor. Never run docket watch or install a wake hook.\n")
    if role == 'reviewer':
        register = f"docket arm {d.name} --role reviewer --session {sid}"
    elif owner and state_of(d, owner)[1] == 'draft':
        dispatch = read_dispatch(d, owner)
        command = 'resume' if dispatch and dispatch.get('session') != sid else 'dispatch'
        register = (f"docket {command} {d.name} {owner} "
                    f"--session {sid} --agent {record['agent_name']} --register")
    else:
        register = f"docket arm {d.name} --role implementor --session {sid}"
    header += ("\nStartup, in this exact order:\n\n"
               f"1. Register first: `{register}`.\n"
               f"2. Then pick up the delivery: `docket pickup {d.name} --role {role} --session {sid}`.\n"
               f"3. Handle the contract below; it is the bound guidance. No role help read is needed.\n\n")
    header += f"Obligation {event['key']}: {event['message']}\n"
    if str(event['key']).startswith('instruction:'):
        iid = str(event['key']).split(':')[1]
        header += f"After handling it, run `docket instruct {d.name} --role {role} --resolve {iid}`.\n"
    if role == 'reviewer':
        owner = owner or next((str(m) for m in batch['members']
                               if state_of(d, str(m))[1] in ('submitted', 'blocked')), '')
    if owner:
        prompt, _info = compose_prompt(d, d.name, owner, role,
                                      stage='review' if role == 'reviewer' else '')
        rendered = conductor_prompt_sections(prompt)
        if section_hashes is not None:
            section_hashes.update({name: hashlib.sha256(body.encode()).hexdigest()
                                   for name, body in rendered.items()})
        previous = record.get('prompt_sections') or {}
        if previous:
            changed = [body for name, body in rendered.items()
                       if not name.startswith('# Steps / ')
                       and previous.get(name) != hashlib.sha256(body.encode()).hexdigest()]
            changed_steps = [body for name, body in rendered.items()
                             if name.startswith('# Steps / ')
                             and previous.get(name) != hashlib.sha256(body.encode()).hexdigest()]
            if changed_steps:
                changed.append('# Steps (changed)\n\n' + '\n'.join(changed_steps))
            removed = [name for name in previous if name not in rendered]
            header += '\nContinued context: retain unchanged contract and duties. Changed sections follow.\n'
            if removed:
                header += 'Retired sections: ' + ', '.join(removed) + '\n'
            header += '\n\n'.join(changed)
        else:
            header += '\n' + prompt
    if role == 'implementor':
        header += "\nAfter submitting, end your turn. The conductor will deliver the next obligation.\n"
    return header


def deliver(d: Path, batch: dict, record: dict, event: dict) -> None:
    role, key = record['role'], str(event['key'])
    if paused_flag(d, role).is_file() or not idle_worker(record):
        return
    section_hashes = {}
    prompt = worker_prompt(d, batch, record, event, section_hashes)
    # This is the same lock used by hook announcement and pickup, including the send.
    with delivery_lock(d, role):
        sweep_role(d, role)
        pending = read_pending(d, role, key)
        if not pending or not event_actionable(d, event)[0]:
            return
        if (announce_delivered(d, role, key) or received_before(d, role, key, pending)
                or announce_active(d, role, key) or event_is_owned(d, role, key)):
            return
        reg = bind_worker_registration(d, record)
        notice = {'key': key, 'role': role, 'run': d.name, 'transport': 'herdr',
                  'message': pending.get('message', ''),
                  **{f: pending.get(f) for f in IDENTITY_FIELDS},
                  'session': record['session'], 'session_generation': reg.get('generation', 1),
                  'announced_at': now_s(), 'lease_until': now_s() + DELIVERY_LEASE_SECONDS}
        publish_json(announce_path(d, role, key), notice)
        delivery_log(d, role, {'kind': 'announced', 'key': key, 'transport': 'herdr'})
        try:
            # Recheck live idle after rendering; never queue text behind a working turn.
            if not idle_worker(record):
                announce_path(d, role, key).unlink(missing_ok=True)
                return
            bind_worker_registration(d, record)
            herdr('agent', 'prompt', record['agent_name'], prompt)
        except HerdrError as exc:
            delivery_log(d, role, {'kind': 'send-failed', 'key': key, 'reason': str(exc)})
            if exc.code in ('agent_busy', 'agent_blocked'):
                return
            raise
        fault('conduct:sent')
        notice['delivered_at'] = now_s()
        publish_json(announce_path(d, role, key), notice)
        remember_receipt(d, role, key, notice)
        ledger = ledger_for(d, role)
        with ledger.open('a+') as log:
            fcntl.flock(log, fcntl.LOCK_EX)
            log.seek(0)
            if key not in log.read().splitlines():
                log.seek(0, os.SEEK_END)
                log.write(key + '\n')
                log.flush()
        if section_hashes:
            record['prompt_sections'] = section_hashes
        record.update(last_delivery_at=stamp(), last_delivery_key=key)
        publish_json(worker_path(d, str(batch['batch']), role), record)
        delivery_log(d, role, {'kind': 'delivered', 'key': key, 'transport': 'herdr',
                               'session': record['session']})


def close_worker(d: Path, batch: dict, role: str, agents: list[dict]) -> None:
    record = read_worker(worker_path(d, str(batch['batch']), role))
    if not record or record.get('closed_at'):
        return
    reg = bind_worker_registration(d, record)
    live = matching_agent(record, agents)
    if live and not idle_worker(record):
        return
    if any(a.get('pane_id') == record.get('pane_id') and a.get('name') != record['agent_name']
           for a in agents):
        die('recorded worker pane has another occupant; inspect it before closing')
    native = reg.get('native_harness') or {}
    record.update(native_session_id=native.get('session_id') or record.get('native_session_id', ''),
                  native_harness=native.get('harness') or record.get('native_harness', ''))
    aliases = [r for r in worker_records(d) if r.get('agent_name') == record['agent_name']
               and r.get('pane_id') == record.get('pane_id')]
    for alias in aliases:
        alias.update(native_session_id=record['native_session_id'], native_harness=record['native_harness'])
        publish_json(worker_path(d, alias['milestone'], role), alias)
    try:
        herdr('pane', 'get', record['pane_id'])
        herdr('pane', 'close', record['pane_id'])
    except HerdrError as exc:
        if exc.code != 'pane_not_found':
            raise
    for alias in aliases:
        alias['closed_at'] = stamp()
        publish_json(worker_path(d, alias['milestone'], role), alias)


def event_batch(event: dict, batches: list[dict]) -> dict:
    key, owner = str(event['key']), str(event.get('owner') or '')
    return next((b for b in batches if owner in b['members'] or
                 key.startswith(f"batch:{b['batch']}:")), {})


def milestone_sort_key(value: str) -> tuple:
    return tuple((1, int(part)) if part.isdigit() else (0, part.lower())
                 for part in re.split(r'(\d+)', value))


def conductor_stall(d: Path, record: dict, reason: str) -> None:
    batch = read_batch(d, str(record['milestone'])) if record.get('milestone') else {}
    members = [str(m) for m in batch.get('members', [])]
    if not members:
        members = [str(m) for b in list_batches(d) for m in b.get('members', [])]
    owner = next((m for m in members if state_of(d, m)[1] not in
                  ('approved', 'waived', 'completed')), next(iter(members), ''))
    if not owner:
        return
    prefix = (f"conductor {record['milestone']} {record['role']} ({record['agent_name']})"
              if record.get('milestone') else 'conductor')
    cause = f'{prefix}: {reason}'
    epoch = reopen_epoch(d, owner, state_of(d, owner)[0])
    if any(i.get('owner') == owner and i.get('cause') == cause and i.get('epoch') == epoch
           and i.get('recipient') == 'planner' and i.get('state') == 'open' for i in list_incidents(d)):
        return
    # Startup has no dispatched work to checkpoint; an unknown pre-dispatch diff
    # would incorrectly turn the first-task event into a resume.
    flag_stall(d, owner, cause, recipient='planner', checkpoint=bool(read_dispatch(d, owner)))


PROVIDER_USAGE_LIMIT = re.compile(
    r"(?:you(?:'ve| have) hit|reached).{0,80}(?:usage|rate|token).{0,30}limit|"
    r"you(?:'ve| have) hit your limit|"
    r"(?:usage|rate|token) limit (?:reached|exceeded)|quota (?:exceeded|exhausted)|"
    r"insufficient_quota|out of (?:extra )?usage|too many requests|"
    r"exceeded.{0,40}(?:quota|usage limit)", re.IGNORECASE)


def monitor_workers(d: Path, batches: list[dict], stall_seconds: float) -> None:
    events = {role: derive_events(d, role) for role in ('implementor', 'reviewer')}
    seen = set()
    records = sorted(worker_records(d), key=lambda r: milestone_sort_key(r['milestone']), reverse=True)
    for record in records:
        name, role = record['agent_name'], record['role']
        if record.get('closed_at') or name in seen:
            continue
        seen.add(name)
        batch = next((b for b in batches if str(b['batch']) == record['milestone']), {})
        if not batch:
            continue
        try:
            agent = herdr('agent', 'get', name)['agent']
            matching_agent(record, [agent])
            status = agent.get('agent_status', 'unknown')
            output = ''
            tail = ''
            if status in ('idle', 'done', 'blocked'):
                snapshot = herdr('agent', 'read', name, '--source', 'detection',
                                 '--lines', '5', raw=True)
                tail = '\n'.join(snapshot.rstrip().splitlines()[-5:])
            if status == 'blocked':
                output = herdr('agent', 'read', name, '--source', 'visible',
                               '--lines', '40', raw=True)
        except HerdrError as exc:
            if exc.code != 'agent_not_found':
                raise
            status, output, tail = 'missing', '', ''
        reasons = []
        provider_limited = bool(PROVIDER_USAGE_LIMIT.search(tail))
        if provider_limited:
            reasons.append('provider usage limit; wait for quota or an explicit planner decision')
        if status == 'blocked':
            text = output.lower()
            trust = any(term in text for term in ('trust this folder', 'trust this directory',
                                                   'trust the contents of this directory'))
            hooks = record['requested_harness'] == 'codex' and 'hooks need review' in text
            if trust or hooks:
                keys = ('down', 'enter') if trust and record['requested_harness'] == 'claude' else ('enter',)
                herdr('agent', 'send-keys', name, *keys)
            elif not provider_limited:
                reasons.append('unrecognized blocked dialog requires user attention')
        obligations = [ev for ev in events[role] if not str(ev['key']).endswith(':approved')
                       and (event_batch(ev, batches).get('batch') == batch['batch']
                            or str(ev['key']).startswith('instruction:'))]
        fragments = [':'.join(str(ev.get(f, '')) for f in ('key', *IDENTITY_FIELDS)) for ev in obligations]
        if role == 'implementor':
            for member in batch['members']:
                dispatch = read_dispatch(d, str(member))
                if state_of(d, str(member))[1] == 'draft' and dispatch.get('session') == record['session']:
                    fragments.append(f"dispatch:{dispatch.get('dispatch')}:{state_of(d, str(member))[0]}")
        signature = hashlib.sha256('|'.join(sorted(fragments)).encode()).hexdigest() if fragments else ''
        now = now_s()
        if signature != record.get('obligation_signature'):
            record.update(obligation_signature=signature, obligation_since=now, idle_since=now)
        if status not in ('idle', 'done', 'missing') or not signature or paused_flag(d, role).is_file():
            record['idle_since'] = 0
        else:
            if not record.get('idle_since'):
                record['idle_since'] = now
            if now - max(float(record['idle_since']), float(record.get('obligation_since', now))) >= stall_seconds:
                delivered = any(announce_delivered(d, role, str(ev['key'])) for ev in obligations)
                label = ('missing worker pane' if status == 'missing' else
                         'unanswered obligation' if delivered or not obligations else 'undelivered obligation')
                reasons.append(f'{label} while idle past {stall_seconds:g}s')
        stall_signature = '|'.join(reasons)
        if stall_signature != record.get('stall_signature'):
            for reason in reasons:
                conductor_stall(d, record, reason)
        record['stall_signature'] = stall_signature
        publish_json(worker_path(d, record['milestone'], role), record)


def conduct_batch_verification(d: Path, batch: dict, event: dict) -> None:
    identity = {key: event.get(key) for key in ('key', 'role', *IDENTITY_FIELDS)}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = conductor_dir(d) / 'verification-refusals' / f'{digest}.json'
    refusal = read_worker(path)
    if not refusal:
        detail = io.StringIO()
        try:
            with contextlib.redirect_stderr(detail):
                cmd_batch_verify(argparse.Namespace(verify=str(batch['batch']), command='', timeout=900,
                    as_role='', verification_actor='conductor'), d)
            return
        except SystemExit as exc:
            reason = detail.getvalue().strip() or str(exc.code)
            refusal = {'event': identity, 'reason': reason, 'at': stamp()}
            publish_json(path, refusal)
    if refusal.get('incident'):
        return
    # Persist the refused identity before flagging, so restart finishes the notice
    # without rerunning an unchanged suite or waking a worker to run it instead.
    cause = (f"conductor milestone {batch['batch']} verification refused [{digest}]: "
             f"{refusal['reason']}; after recovery run "
             f"docket batch {d.name} --verify {batch['batch']} --as implementor")
    incident = next((item for item in list_incidents(d)
                     if item.get('cause') == cause and item.get('recipient') == 'planner'), None)
    if incident is None:
        owner = next((str(member) for member in batch['members']
                      if state_of(d, str(member))[1] not in ('approved', 'waived', 'completed')),
                     str(batch['members'][0]))
        incident = flag_stall(d, owner, cause, recipient='planner', checkpoint=False)
    publish_json(path, {**refusal, 'incident': incident['id']})


def conduct_once(d: Path, stall_seconds: float = 300) -> bool:
    agents = herdr('agent', 'list')['agents']
    batches = sorted([b for b in list_batches(d) if b.get('milestone') and b.get('state') == 'closed'],
                     key=lambda b: milestone_sort_key(str(b['batch'])))
    warning_path = conductor_dir(d) / 'budget.json'
    warnings = {key: message for key, message, _revision in codex_budget_events(d, actionable=False)}
    previous_warnings = read_worker(warning_path)
    for key, message in warnings.items():
        if previous_warnings.get(key) != message:
            print(message, flush=True)
    if warnings != previous_warnings:
        publish_json(warning_path, warnings)
    implementation = derive_events(d, 'implementor')
    for event in implementation:
        if ':verify-ready:' in str(event['key']):
            bid = str(event['key']).split(':')[1]
            conduct_batch_verification(d, next(b for b in batches if str(b['batch']) == bid), event)
    implementation = derive_events(d, 'implementor')
    approvals = {str(ev['key']).split(':')[1] for ev in implementation
                 if str(ev['key']).startswith('batch:') and str(ev['key']).endswith(':approved')}
    planner = derive_events(d, 'planner')
    complete = any(str(ev['key']).startswith('milestones:complete:') for ev in planner)
    cleanup_pending = False
    active = [b for b in batches if str(b['batch']) not in approvals]
    next_batch = next(iter(active), {})
    candidate = read_worker(worker_path(d, str(next_batch['batch']), 'reviewer')) if next_batch else {}
    if not candidate and next_batch.get('reviewer_context') == 'continue':
        candidate = previous_reviewer(d, next_batch)
    reuse_name = candidate.get('agent_name') if candidate and not candidate.get('closed_at') else ''
    for batch in batches:
        if str(batch['batch']) in approvals:
            roles = ['implementor']
            reviewer = read_worker(worker_path(d, str(batch['batch']), 'reviewer'))
            if complete or batch.get('reviewer_context') == 'fresh' or reviewer.get('agent_name') != reuse_name:
                roles.append('reviewer')
            for role in roles:
                existing = read_worker(worker_path(d, str(batch['batch']), role))
                if existing.get('manual_reopen') and not complete:
                    continue
                close_worker(d, batch, role, agents)
                record = read_worker(worker_path(d, str(batch['batch']), role))
                if record and not record.get('closed_at'):
                    cleanup_pending = True
    if complete:
        monitor_workers(d, batches, stall_seconds)
        if not cleanup_pending:
            print(f'conductor {d.name}: run complete; all milestones have intact reviewer approval', flush=True)
        return not cleanup_pending
    for role, derived in (('implementor', implementation), ('reviewer', derive_events(d, 'reviewer'))):
        for event in derived:
            key = str(event['key'])
            if key.endswith(':approved') or key.startswith('launch:') or ':verify-ready:' in key:
                continue
            batch = event_batch(event, batches)
            if not batch and key.startswith('instruction:'):
                candidates = [b for b in batches if str(b['batch']) not in approvals]
                batch = next((b for b in candidates if worker_path(
                    d, str(b['batch']), role).is_file()), next(iter(candidates), {}))
            if not batch:
                continue
            if key.endswith(':codex-limit'):
                record = read_worker(worker_path(d, str(batch['batch']), role))
                if record:
                    conductor_stall(d, record, 'provider usage limit; wait for quota or an explicit planner decision')
                continue
            if role == 'implementor' and event.get('owner') and quick_milestone_implementation_problem(
                    d, str(event['owner'])):
                continue
            record = ensure_worker(d, batch, role, agents)
            if record:
                deliver(d, batch, record, event)
    monitor_workers(d, batches, stall_seconds)
    return False


def cmd_conduct(a: argparse.Namespace) -> None:
    if os.environ.get('HERDR_ENV') != '1':
        die('docket conduct requires a Herdr pane (HERDR_ENV=1)')
    d = need_run(a.run)
    if not is_quick_milestone(d):
        die('docket conduct supports quick milestone runs with recorded assignments only')
    if plan_flag(d, 'round_context', '') != 'shared':
        die('docket conduct requires round_context: shared; preserve existing subagent-policy runs for manual launch')
    if a.interval <= 0 or not math.isfinite(a.interval):
        die('--interval must be positive')
    batches = [b for b in list_batches(d) if b.get('milestone')]
    if not batches or not batches[0].get('members'):
        die('assign every planned task and close its milestone before starting the conductor')
    if any(b.get('state') != 'closed' or not b.get('assignment_version') for b in batches):
        die('close every milestone with recorded role assignments before starting the conductor')
    problems = quick_milestone_plan_problems(d, str(batches[0]['members'][0]))
    if problems:
        die('conductor cannot launch before planning is complete: ' + '; '.join(problems))
    for name in ('stall_seconds', 'outage_seconds'):
        value = getattr(a, name)
        if value <= 0 or not math.isfinite(value):
            die(f"--{name.replace('_', '-')} must be positive")
    with conductor_lock(d):
        path = conductor_dir(d) / 'managed.json'
        managed = {**read_worker(path), 'run': d.name, 'started_at': stamp(),
                   'pid': os.getpid(), 'transport': 'herdr', 'outage_since': 0}
        publish_json(path, managed)
        failures = 0
        try:
            while True:
                try:
                    with owner_lock(d, 'conductor-workers'):
                        complete = conduct_once(d, a.stall_seconds)
                    managed.update(outage_since=0, last_error='', last_success=stamp())
                    publish_json(path, managed)
                    failures = 0
                    if complete or a.once:
                        return
                    time.sleep(a.interval)
                except HerdrError as exc:
                    now = now_s()
                    if not managed.get('outage_since'):
                        managed['outage_since'] = now
                    managed['last_error'] = str(exc)
                    publish_json(path, managed)
                    elapsed = now - float(managed['outage_since'])
                    if elapsed >= a.outage_seconds:
                        conductor_stall(d, {}, 'sustained Herdr outage; restart the conductor after restoring Herdr')
                        die(f'conductor {d.name}: sustained Herdr outage after {elapsed:.1f}s: {exc}')
                    failures += 1
                    delay = min(30, a.interval * 2 ** min(failures - 1, 10), a.outage_seconds - elapsed)
                    print(f'conductor {d.name}: Herdr unavailable; retry in {delay:g}s', file=sys.stderr, flush=True)
                    time.sleep(delay)
        except KeyboardInterrupt:
            print(f'conductor {d.name}: stopped by interrupt; workers remain recorded', file=sys.stderr)


def worker_native_identity(d: Path, record: dict, batch: dict) -> tuple[str, str]:
    reg = read_registration(record['session'])
    if reg and (reg.get('run') != d.name or reg.get('role') != record['role']
                or reg.get('workspace') != str(root().resolve())):
        die('recorded worker registration belongs to another run, role, or workspace; cannot reopen')
    native = reg.get('native_harness') or {}
    harness = native.get('harness') or record.get('native_harness') or record['requested_harness']
    sid = native.get('session_id') or record.get('native_session_id') or ''
    if not sid:
        candidates = [ref for ref in run_harness_sessions(d)
                      if record['role'] in ref.get('roles', [])
                      and ref.get('harness') == harness
                      and set(ref.get('owners', [])) & set(batch['members'])]
        if len(candidates) == 1:
            sid = str(candidates[0]['session_id'])
    if not sid:
        die(f"no recorded native session identity for {batch['batch']} {record['role']}; cannot reopen")
    if harness != record['requested_harness'] or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', sid):
        die('recorded native session identity conflicts with the worker launch; inspect before reopening')
    return harness, sid


def reopen_worker(d: Path, batch: dict, role: str) -> None:
    bid = str(batch['batch'])
    path = worker_path(d, bid, role)
    record = read_worker(path)
    if not record:
        die(f'no recorded {role} launch for milestone {bid}; cannot reopen')
    harness, native = worker_native_identity(d, record, batch)
    allowed = allowed_models_for_role(d, role, str(batch['members'][0]))
    if allowed is not None and record['requested_model'] not in allowed:
        die('the recorded model is no longer approved by the current plan; resolve its policy before reopening')
    guard_review_reserve(d, record['requested_model'])
    agents = herdr('agent', 'list')['agents']
    if matching_agent(record, agents):
        print(f"{bid} {role} is already open in {record['pane_id']}")
        return
    if record.get('pane_id') and not record.get('closed_at') and not record.get('reopen_pending'):
        close_worker(d, batch, role, agents)
        record = read_worker(path)
    previous_session = record['session']
    aliases = [r for r in worker_records(d) if r['session'] == previous_session]
    if not record.get('reopen_pending'):
        count = int(record.get('reopen_count', 0)) + 1
        base = record.get('original_session') or previous_session
        record.update(original_session=base, session=f'{base}-reopen-{count}',
                      previous_session=previous_session, reopen_count=count, reopen_pending=True,
                      native_harness=harness, native_session_id=native, resume_native_id=native,
                      pane_id='', started_at='', closed_at='', reopened_at=stamp(),
                      last_delivery_at='', last_delivery_key='',
                      manual_reopen=any(ev['key'] == f'batch:{bid}:approved'
                                        for ev in derive_events(d, 'implementor')))
        for field in ('session_generation', 'obligation_signature', 'obligation_since', 'idle_since'):
            record.pop(field, None)
        publish_json(path, record)
    if not record.get('pane_id'):
        record['pane_id'] = worker_pane(d, role, record['session'])
        publish_json(path, record)
    for alias in aliases:
        updated = {**record, 'milestone': alias['milestone']}
        publish_json(worker_path(d, alias['milestone'], role), updated)
    with delivery_lock(d, role):
        sweep_role(d, role)
        retry = [str(ev['key']) for ev in derive_events(d, role)
                 if read_announce(d, role, str(ev['key'])).get('session') == previous_session]
    for key in retry:
        inbox_retry(argparse.Namespace(role=role, run=d.name, retry=key,
                                      reason='the user reopened the recorded native session'), d)
    start_worker(d, record)
    record['reopen_pending'] = False
    for alias in aliases:
        publish_json(worker_path(d, alias['milestone'], role), {**record, 'milestone': alias['milestone']})
    print(f"reopened {bid} {role} native session {native} in {record['pane_id']}; "
          'the resumed worker must register itself before acting')


def cmd_reopen(a: argparse.Namespace) -> None:
    if os.environ.get('HERDR_ENV') != '1':
        die('docket reopen requires a Herdr pane (HERDR_ENV=1)')
    d = need_run(a.run)
    batch_path(d, a.milestone)
    batch = read_batch(d, a.milestone)
    if not batch.get('milestone'):
        die(f'no milestone {a.milestone} in {a.run}')
    try:
        with owner_lock(d, 'conductor-workers'):
            reopen_worker(d, batch, a.role)
    except HerdrError as exc:
        die(f'reopen could not reach Herdr: {exc}; retry to reconcile its recorded launch')
