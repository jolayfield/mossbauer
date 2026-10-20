"""Dependency-free cluster harvesting. Also shipped inside harvest.pyz with the parser."""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import hashlib
import json
import math
from pathlib import Path
import re

from . import __version__
from .orca_parse import OrcaResult, FeSite, Fingerprint, HyperfineTensor, parse_orca_text

FORMAT = 'mossbauer-compact/1'
MAX_BYTES = 5_000_000
STAMP = re.compile(r'^\|\s*\d+>\s*#\s*mossbauer-dft:\s+entry=(\S+)\s+version=(\d+)\s+plan=([a-f0-9]+)\s*$', re.M)
SAFE = re.compile(r'[A-Za-z0-9_-]+\Z')


STATE_MAX = 4096
STATE_KEYS = {'started': ('job', 'node', 'at'), 'finished': ('rc', 'at', 'reason')}


def parse_state(text):
    """A job-state file written by the Slurm script: 'started job=.. node=.. at=..' then 'finished rc=.. at=.. reason=..'.
    The last line of each kind wins; unknown lines are ignored. Returns {'started': {...}, 'finished': {...}} (either optional)."""
    out = {}
    for line in str(text)[:STATE_MAX].splitlines():
        event, _, rest = line.strip().partition(' ')
        if event not in STATE_KEYS: continue
        fields_ = {}
        head, sep, reason = rest.partition('reason=')
        for tok in head.split():
            k, eq, v = tok.partition('=')
            if eq and k in STATE_KEYS[event]: fields_[k] = v[:200]
        if sep and 'reason' in STATE_KEYS[event]: fields_['reason'] = reason.strip()[:200]
        out[event] = fields_
    return out


def check_state(state):
    if not isinstance(state, dict) or not set(state) <= set(STATE_KEYS):
        raise ValueError('invalid job-state record')
    for event, values in state.items():
        if not isinstance(values, dict) or not set(values) <= set(STATE_KEYS[event]) or \
                any(not isinstance(v, str) or len(v) > 200 for v in values.values()):
            raise ValueError('invalid job-state record')
    return state


def read_json(path):
    p = Path(path)
    if p.stat().st_size > MAX_BYTES: raise ValueError('compact document exceeds 5 MB')
    return json.loads(p.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def _finite(value, depth=0):
    if depth > 20: raise ValueError('compact document nesting is too deep')
    if isinstance(value, float) and not math.isfinite(value): raise ValueError('nonfinite compact number')
    if isinstance(value, dict):
        for v in value.values(): _finite(v, depth+1)
    elif isinstance(value, list):
        for v in value: _finite(v, depth+1)


def decode(record, n_atoms):
    _finite(record)
    if not isinstance(record, dict) or not isinstance(record.get('parsed'), dict):
        raise ValueError('invalid parsed result')
    data = dict(record['parsed'])
    if set(data) != {f.name for f in fields(OrcaResult)}: raise ValueError('invalid parsed-result fields')
    for k in ('normal_termination', 'error_termination', 'is_optimization'):
        if type(data[k]) is not bool: raise ValueError('invalid termination flags')
    for k in ('scf_converged', 'geom_converged'):
        if data[k] is not None and type(data[k]) is not bool: raise ValueError('invalid convergence flag')
    for k in ('final_energy', 's2', 's2_ideal'):
        if data[k] is not None and type(data[k]) not in (int, float): raise ValueError('invalid scientific number')
    if not isinstance(data['fe_sites'], list) or len(data['fe_sites']) > n_atoms: raise ValueError('invalid Fe-site count')
    for key in ('path', 'program'):
        if not isinstance(data[key], str): raise ValueError('invalid result text')
    for key in ('charge', 'multiplicity', 'scf_cycles', 'n_scf_runs'):
        if data[key] is not None and type(data[key]) is not int: raise ValueError('invalid result integer')
    if not isinstance(data['warnings'], list) or any(not isinstance(v, str) for v in data['warnings']):
        raise ValueError('invalid result warnings')
    if not isinstance(data['hyperfine'], list) or not isinstance(data['zfs'], list):
        raise ValueError('invalid tensor collections')
    sites, indices = [], set()
    for site in data['fe_sites']:
        if set(site) != {f.name for f in fields(FeSite)}: raise ValueError('invalid Fe-site fields')
        i = site['index']
        if type(i) is not int or not 0 <= i < n_atoms or i in indices or site['element'] != 'Fe':
            raise ValueError('invalid Fe-site identity')
        indices.add(i)
        for key, value in site.items():
            if key in ('index', 'element', 'v_matrix', 'v_eigs_orca'): continue
            if value is not None and type(value) not in (int, float): raise ValueError('invalid Fe-site value')
        matrix = site['v_matrix']
        if matrix is not None and (not isinstance(matrix, list) or len(matrix) != 3 or
                any(not isinstance(r, list) or len(r) != 3 or any(type(v) not in (int, float) for v in r) for r in matrix)):
            raise ValueError('invalid EFG matrix')
        eigs = site['v_eigs_orca']
        if eigs is not None and (not isinstance(eigs, list) or len(eigs) != 3 or any(type(v) not in (int,float) for v in eigs)):
            raise ValueError('invalid EFG eigenvalues')
        sites.append(FeSite(**site))
    for h in data['hyperfine']:
        if not isinstance(h, dict) or set(h) != {f.name for f in fields(HyperfineTensor)}:
            raise ValueError('invalid hyperfine tensor')
        if type(h['nucleus_index']) is not int or not 0 <= h['nucleus_index'] < n_atoms or not isinstance(h['element'], str):
            raise ValueError('invalid hyperfine nucleus')
    fp = data['fingerprint']
    if not isinstance(fp, dict) or set(fp) != {f.name for f in fields(Fingerprint)} or any(not isinstance(v,str) or len(v)>512 for v in fp.values()):
        raise ValueError('invalid fingerprint')
    data.update(fingerprint=Fingerprint(**fp), fe_sites=sites, hyperfine=[HyperfineTensor(**h) for h in data['hyperfine']])
    return OrcaResult(**data)


def validate(doc, manifest):
    if not isinstance(doc, dict) or doc.get('format') != FORMAT or not isinstance(doc.get('records'), dict):
        raise ValueError('unsupported compact-result schema')
    for key in ('entry', 'version', 'plan_hash'):
        if doc.get(key) != manifest.get(key): raise ValueError(f'compact {key} does not match this plan version')
    if not isinstance(doc.get('parser_version'), str) or not doc['parser_version']: raise ValueError('parser version missing')
    missing, rejected = doc.get('missing', []), doc.get('rejected', [])
    if not isinstance(missing, list) or any(not isinstance(s, str) for s in missing):
        raise ValueError('invalid missing-job list')
    if not isinstance(rejected, list) or any(not isinstance(r, dict) or not isinstance(r.get('job'), str)
                                            or not isinstance(r.get('reason'), str) for r in rejected):
        raise ValueError('invalid rejected-job list')
    expected = {j['stem']: j for j in manifest['jobs']}
    if len(doc['records']) > len(expected): raise ValueError('too many compact jobs')
    census = doc.get('census', {})
    if not isinstance(census, dict) or any(stem not in expected for stem in census):
        raise ValueError('invalid job-state census')
    for state in census.values(): check_state(state)
    result = {}
    for stem, record in doc['records'].items():
        if not SAFE.fullmatch(stem) or stem not in expected: raise ValueError('unrecognized compact job')
        if not isinstance(record, dict) or not isinstance(record.get('source'), dict):
            raise ValueError('invalid source record')
        src = record['source']
        if src['name'] != stem+'.out' or type(src['size']) is not int or src['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', src['sha256']):
            raise ValueError('invalid source-output identity')
        if record.get('stamp') != dict(entry=doc['entry'], version=doc['version'], plan=doc['plan_hash'][:12]):
            raise ValueError('missing or mismatched echoed provenance stamp')
        parsed = decode(record, manifest['n_atoms'])
        if any(s.index not in manifest['fe_indices'] for s in parsed.fe_sites):
            raise ValueError('compact Fe site differs from the planned structure')
        job = expected[stem]
        if parsed.charge != job['charge'] or parsed.multiplicity != job['multiplicity']:
            raise ValueError('compact electronic state differs from expected job')
        hard, soft = Fingerprint.from_dict(job['fingerprint']).compare(parsed.fingerprint)
        if hard or soft: raise ValueError('compact fingerprint differs from expected job: '+'; '.join(hard+soft))
        result[stem] = parsed
    return result


def harvest(manifest_path, outputs):
    manifest = read_json(manifest_path)
    doc = dict(format=FORMAT, parser_version=__version__, entry=manifest['entry'], version=manifest['version'],
               plan_hash=manifest['plan_hash'], records={}, missing=[], rejected=[], census={})
    for job in manifest['jobs']:
        stem = job['stem']
        if not SAFE.fullmatch(stem): raise ValueError('unsafe expected job stem')
        state_file = Path(outputs) / f'{stem}.state'
        if state_file.is_file():
            with open(state_file, errors='replace') as fh: state = parse_state(fh.read(STATE_MAX))
            if state: doc['census'][stem] = state
        source = Path(outputs) / f'{stem}.out'
        if not source.is_file():
            doc['missing'].append(stem); continue
        raw = source.read_bytes()
        text = raw.decode(errors='replace')
        match = STAMP.search(text)
        try:
            # one unparseable output is a rejected job, not a failed harvest
            parsed = json.loads(json.dumps(asdict(parse_orca_text(text, source.name)), allow_nan=False))
            parsed['path'] = source.name
            record = dict(source=dict(name=source.name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()), parsed=parsed,
                          stamp=dict(entry=match[1], version=int(match[2]), plan=match[3]) if match else None)
            validate(dict(doc, records={stem: record}), manifest)
        except Exception as ex:  # noqa: BLE001
            doc['rejected'].append(dict(job=stem, reason=str(ex))); continue
        doc['records'][stem] = record
    return doc


def main():
    parser = argparse.ArgumentParser(description='Harvest compact evidence on a cluster with Python standard library only.')
    parser.add_argument('manifest'); parser.add_argument('outputs'); parser.add_argument('destination')
    a = parser.parse_args()
    body = json.dumps(harvest(a.manifest, a.outputs), indent=2, allow_nan=False)
    if len(body.encode()) > MAX_BYTES:
        raise SystemExit('bundle exceeds the 5 MB import limit; harvest fewer outputs per bundle')
    p = Path(a.destination)
    if p.exists(): raise SystemExit('destination exists; choose a new bundle filename')
    p.write_text(body)
    print(str(p))


if __name__ == '__main__': main()
