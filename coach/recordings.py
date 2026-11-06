"""Read-only recording metadata. Separate captures are never concatenated."""
import json
import re


def valid_ms(value):
    return type(value) is int and 0 <= value <= 2**53-1


def groups(rows, marks, active, sid):
    parts = {}
    for row in rows:
        key = (row['kind'], row['segment'])
        group = parts.setdefault(key, dict(kind=key[0], segment=key[1], start_ms=row['start_ms'], mime=row['mime'], seqs=[], bytes=0))
        group['seqs'].append(row['seq'])
        group['bytes'] += row['size']
    starts, stops, ticks, disconnects = {}, {}, {}, []
    for mark in marks:
        payload = json.loads(mark['payload'])
        kind, segment = payload.get('kind', 'screen'), payload.get('segment')
        if mark['kind'] == 'recording_gap' and valid_ms(mark['ms']):
            disconnects.append((kind, segment, mark['ms']))
        if kind not in {'screen', 'voice'} or not isinstance(segment, str) or not re.fullmatch('[a-f0-9]{32}', segment):
            continue
        key = (kind, segment)
        if mark['kind'] == 'recording_start':
            offset = payload.get('offset_ms', mark['ms'])
            if valid_ms(offset): starts[key] = offset
        elif mark['kind'] == 'recording_chunk':
            seq, end = payload.get('seq'), payload.get('end_ms')
            if type(seq) is int and 0 <= seq < 100000 and valid_ms(end): ticks.setdefault(key, {})[seq] = end
        elif mark['kind'] == 'recording_stop' and type(payload.get('chunks')) is int and 0 <= payload['chunks'] <= 100000:
            end = payload.get('end_ms', mark['ms'])
            if valid_ms(end) and (key not in stops or payload['chunks'] >= stops[key]['chunks']):
                stops[key] = dict(chunks=payload['chunks'], end_ms=end, ended='interrupted' if payload.get('state') == 'interrupted' else 'normal')
    # Even a start followed by a closed tab before the first chunk is evidence.
    for key in starts.keys() | stops.keys():
        parts.setdefault(key, dict(kind=key[0], segment=key[1], start_ms=starts.get(key, 0), mime=None, seqs=[], bytes=0))
    result = []
    for key, group in parts.items():
        seqs = sorted(group.pop('seqs')); present = set(seqs); last = max(seqs, default=-1); stop = stops.get(key)
        expected = stop['chunks'] if stop else None
        missing = [i for i in range(max(last + 1, expected or 0)) if i not in present]
        superseded = any(k[0] == key[0] and value > group['start_ms'] for k, value in starts.items()) or any(kind == key[0] and (segment is None or segment == key[1]) and ms >= group['start_ms'] for kind, segment, ms in disconnects)
        if missing or (not seqs and expected != 0): status = 'pending' if active and not superseded else 'missing'
        elif expected is None: status = 'recording' if active and not superseded else 'unconfirmed'
        else: status = 'complete' if last + 1 == expected else 'unconfirmed'
        playable = bool(seqs) and seqs == list(range(len(seqs)))
        stored_end = ticks.get(key, {}).get(last) if playable else None
        capture_end = stop['end_ms'] if stop else None
        end = stored_end if stored_end is not None else capture_end if status == 'complete' and playable else None
        if end is not None and end < group['start_ms']: end = None
        result.append(group | dict(chunks=len(seqs), last=last, expected_chunks=expected,
            ended=stop['ended'] if stop else None, status=status, complete_sequence=status == 'complete',
            missing=missing[:50], missing_count=len(missing), playable=playable,
            end_ms=end, capture_end_ms=capture_end, end_confirmed=bool(stop),
            url=f'/api/v2/media/{sid}/{key[0]}/{key[1]}' if playable else None))
    return sorted(result, key=lambda g: (g['start_ms'], g['segment']))


def gaps(groups, end_ms):
    """Known missing time and uncertain coverage, never reconstructed footage."""
    screens = [g for g in groups if g['kind'] == 'screen']
    result, cursor = [], 0
    def add(start, end, kind):
        start, end = max(0, start), min(end_ms, end)
        if end - start > 250: result.append(dict(start_ms=start, end_ms=end, kind=kind))
    for i, group in enumerate(screens):
        start = group['start_ms']; bound = screens[i + 1]['start_ms'] if i + 1 < len(screens) else end_ms
        add(cursor, start, 'not_recorded')
        recorded_end = group.get('end_ms')
        if recorded_end is None:
            add(start, bound, 'unconfirmed' if group['playable'] else 'missing')
            cursor = max(cursor, bound)
        else:
            cursor = max(cursor, recorded_end)
            if group['status'] != 'complete':
                add(cursor, bound, 'unconfirmed' if group['status'] in {'recording', 'unconfirmed'} else 'missing')
                cursor = max(cursor, bound)
    add(cursor, end_ms, 'not_recorded')
    return result
