import os, sqlite3
from flask import Flask, request, jsonify, g, send_from_directory

DB_PATH = os.environ.get('DB_PATH', '/data/profili.db')
TAPPI_PER_SCHEDA = 5
DA_CP_DIVISO = 6
BAR_LENGTH = 3000
MAX_CP = 10

app = Flask(__name__, static_folder=None)

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def _close(e=None):
    db = g.pop('db', None)
    if db: db.close()

PIECE_LENGTHS = {
    1: [165],
    2: [315],
    3: [455],
    4: [615],
    5: [765],
    6: [457, 457],
    7: [612, 462],
    8: [607, 607],
    9: [772, 622],
    10: [757, 757]
}

def get_pieces_for_cp(cp_num):
    lengths = PIECE_LENGTHS.get(cp_num)
    if not lengths:
        return []
    counts = {}
    for l in lengths:
        counts[l] = counts.get(l, 0) + 1
    return [(l, q) for l, q in counts.items()]

def init_db():
    d = os.path.dirname(DB_PATH)
    if d: os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript('''
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            cp_num INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            variante TEXT NOT NULL,
            pieces INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS profile_components (
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY (profile_id, name)
        );
        CREATE TABLE IF NOT EXISTS profile_pieces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            length_mm REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1
        );
    ''')
    cur = con.execute("SELECT id, cp_num FROM profiles WHERE tipo='tappi'")
    for row in cur.fetchall():
        pid, cp_num = row[0], row[1]
        if con.execute("SELECT 1 FROM profile_pieces WHERE profile_id=?", (pid,)).fetchone():
            continue
        for length, qty in get_pieces_for_cp(cp_num):
            con.execute("INSERT INTO profile_pieces (profile_id, length_mm, quantity) VALUES (?,?,?)",
                        (pid, length, qty))
    con.execute("DELETE FROM profile_components WHERE name LIKE 'maschiatura %'")
    con.commit(); con.close()

def profile_dict(con, row):
    comps = con.execute(
        'SELECT name, quantity FROM profile_components WHERE profile_id=? ORDER BY name',
        (row['id'],)
    ).fetchall()
    pieces = con.execute(
        'SELECT length_mm, quantity FROM profile_pieces WHERE profile_id=? ORDER BY length_mm DESC',
        (row['id'],)
    ).fetchall()
    return {
        'id': row['id'], 'code': row['code'],
        'cp_num': row['cp_num'], 'tipo': row['tipo'],
        'variante': row['variante'], 'pieces': row['pieces'],
        'components': [{'name': c['name'], 'quantity': c['quantity']} for c in comps],
        'pieces_detail': [{'length_mm': p['length_mm'], 'quantity': p['quantity']} for p in pieces]
    }

@app.route('/api/profiles', methods=['GET'])
def api_list():
    con = get_db()
    rows = con.execute('SELECT * FROM profiles ORDER BY tipo, cp_num, variante').fetchall()
    return jsonify([profile_dict(con, r) for r in rows])

@app.route('/api/profiles', methods=['POST'])
def api_create():
    data = request.get_json() or {}
    try:
        cp_num = int(data['cp_num']); tipo = data['tipo'].lower(); variante = data['variante'].lower()
    except (KeyError, ValueError):
        return jsonify({'error': 'cp_num, tipo, variante richiesti'}), 400
    if not 1 <= cp_num <= MAX_CP:
        return jsonify({'error': f'cp deve essere tra 1 e {MAX_CP}'}), 400
    if tipo == 'schede' and variante not in ('dritte', 'smussi'):
        return jsonify({'error': 'variante schede: dritte o smussi'}), 400
    if tipo == 'tappi' and variante not in ('nero', 'bianco'):
        return jsonify({'error': 'variante tappi: nero o bianco'}), 400

    code = f"cp{cp_num:02d} {tipo} {variante}"
    pieces = 2 if cp_num >= DA_CP_DIVISO else 1

    if data.get('components'):
        comps = data['components']
    elif tipo == 'schede':
        comps = [{'name': f"scheda {variante}", 'quantity': cp_num}]
    else:
        comps = [{'name': f"tappo {variante}", 'quantity': cp_num * TAPPI_PER_SCHEDA}]

    con = get_db()
    try:
        cur = con.execute('INSERT INTO profiles (code,cp_num,tipo,variante,pieces) VALUES (?,?,?,?,?)',
                          (code, cp_num, tipo, variante, pieces))
        pid = cur.lastrowid
        for c in comps:
            con.execute('INSERT INTO profile_components (profile_id,name,quantity) VALUES (?,?,?)',
                        (pid, c['name'], int(c['quantity'])))
        if tipo == 'tappi':
            for length, qty in get_pieces_for_cp(cp_num):
                con.execute("INSERT INTO profile_pieces (profile_id, length_mm, quantity) VALUES (?,?,?)",
                            (pid, length, qty))
        con.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': f'Profilo "{code}" esiste già'}), 409
    row = con.execute('SELECT * FROM profiles WHERE id=?', (pid,)).fetchone()
    return jsonify(profile_dict(con, row)), 201

@app.route('/api/profiles/<int:pid>', methods=['DELETE'])
def api_delete(pid):
    con = get_db(); con.execute('DELETE FROM profiles WHERE id=?', (pid,)); con.commit()
    return '', 204

@app.route('/api/profiles/<int:pid>/pieces', methods=['PUT'])
def api_update_pieces(pid):
    """Corregge le misure dei pezzi di un profilo tappi, senza ricrearlo."""
    con = get_db()
    row = con.execute('SELECT * FROM profiles WHERE id=?', (pid,)).fetchone()
    if not row:
        return jsonify({'error': 'profilo non trovato'}), 404
    if row['tipo'] != 'tappi':
        return jsonify({'error': 'i pezzi si modificano solo sui profili tappi'}), 400

    data = request.get_json() or {}
    items = data.get('pieces')
    if not isinstance(items, list) or not items:
        return jsonify({'error': 'serve almeno un pezzo'}), 400

    parsed = []
    for p in items:
        try:
            length = float(p['length_mm']); qty = int(p['quantity'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'error': 'ogni pezzo richiede length_mm e quantity numerici'}), 400
        if length <= 0 or length > BAR_LENGTH:
            return jsonify({'error': f'lunghezza non valida: {length} (ammesso 0–{BAR_LENGTH} mm)'}), 400
        if qty < 1:
            return jsonify({'error': 'la quantità di un pezzo deve essere almeno 1'}), 400
        parsed.append((length, qty))

    con.execute('DELETE FROM profile_pieces WHERE profile_id=?', (pid,))
    for length, qty in parsed:
        con.execute('INSERT INTO profile_pieces (profile_id, length_mm, quantity) VALUES (?,?,?)',
                    (pid, length, qty))
    con.commit()
    row = con.execute('SELECT * FROM profiles WHERE id=?', (pid,)).fetchone()
    return jsonify(profile_dict(con, row))


import time
from collections import Counter

MAX_DP_STATES = 20000
MAX_SECONDS = 2.0

def _fits(total_len, n_pieces, kerf):
    # il kerf si conta solo tra un pezzo e l'altro (n-1 tagli), come nella versione precedente
    return total_len + max(0, n_pieces - 1) * kerf <= BAR_LENGTH + 1e-9

def _maximal_patterns(lengths, demands, kerf):
    """Tutte le combinazioni 'massimali' di pezzi che stanno in una asta."""
    pats = []
    k = len(lengths)
    def rec(i, counts, used_len, used_n):
        if i == k:
            # massimale: non si può aggiungere nessun altro pezzo ancora richiesto
            for j in range(k):
                if counts[j] < demands[j] and _fits(used_len + lengths[j], used_n + 1, kerf):
                    return
            if used_n > 0:
                pats.append(tuple(counts))
            return
        n = 0
        while n <= demands[i] and _fits(used_len + n * lengths[i], used_n + n, kerf):
            counts.append(n)
            rec(i + 1, counts, used_len + n * lengths[i], used_n + n)
            counts.pop()
            n += 1
    rec(0, [], 0.0, 0)
    return pats

def _ffd(pieces, kerf):
    """Vecchio algoritmo First Fit Decreasing, usato come riserva. pieces = lista di lunghezze."""
    bars = []
    for length in sorted(pieces, reverse=True):
        for bar in bars:
            if _fits(bar['sum'] + length, len(bar['p']) + 1, kerf):
                bar['p'].append(length); bar['sum'] += length
                break
        else:
            bars.append({'p': [length], 'sum': length})
    return [b['p'] for b in bars]

def _optimize_dp(pieces, kerf):
    """pieces: lista di lunghezze. Ritorna lista di aste, ognuna lista di lunghezze.
    Cerca il minimo numero di aste con una ricerca esatta sugli schemi di taglio;
    se lo spazio di ricerca è troppo grande ripiega su FFD."""
    cnt = Counter(pieces)
    lengths = sorted(cnt.keys(), reverse=True)
    demands = [cnt[l] for l in lengths]
    pats = _maximal_patterns(lengths, demands, kerf)
    if not pats:
        return _ffd(pieces, kerf)

    memo = {}
    states = [0]
    t0 = time.time()
    class TooBig(Exception): pass

    def solve(rem):
        # numero minimo di aste per coprire i pezzi rimanenti
        if not any(rem):
            return 0, None
        if rem in memo:
            return memo[rem]
        states[0] += 1
        if states[0] > MAX_DP_STATES or time.time() - t0 > MAX_SECONDS:
            raise TooBig()
        i0 = next(i for i, r in enumerate(rem) if r)   # il pezzo più lungo ancora da tagliare
        best = (10**9, None)
        seen = set()
        for p in pats:
            if p[i0] == 0:
                continue
            take = tuple(min(p[j], rem[j]) for j in range(len(rem)))
            if take in seen:
                continue
            seen.add(take)
            nxt = tuple(rem[j] - take[j] for j in range(len(rem)))
            sub, _ = solve(nxt)
            if sub + 1 < best[0]:
                best = (sub + 1, take)
        memo[rem] = best
        return best

    try:
        total, _ = solve(tuple(demands))
    except TooBig:
        return _ffd(pieces, kerf)

    # ricostruzione delle aste
    bars = []
    rem = tuple(demands)
    while any(rem):
        _, take = memo[rem]
        bar = []
        for j, n in enumerate(take):
            bar += [lengths[j]] * n
        bars.append(bar)
        rem = tuple(rem[j] - take[j] for j in range(len(rem)))
    # prima le aste più piene, l'eventuale asta con avanzo grande resta per ultima
    bars.sort(key=lambda b: (-len(b), -sum(b)))
    return bars

ILP_MAX_PATTERNS = 20000

def _optimize_ilp(pieces, kerf):
    """Ottimo con programmazione lineare intera (scipy/HiGHS).
    Fase 1: minimo numero di aste. Fase 2: a parità di aste, meno schemi di taglio diversi
    (così più aste identiche si possono tagliare insieme). Ritorna None se non disponibile."""
    try:
        import numpy as np
        from scipy.optimize import milp, LinearConstraint, Bounds
    except Exception:
        return None
    cnt = Counter(pieces)
    lengths = sorted(cnt.keys(), reverse=True)
    d = np.array([cnt[l] for l in lengths], dtype=float)
    pats = _maximal_patterns(lengths, [int(x) for x in d], kerf)
    if not pats or len(pats) > ILP_MAX_PATTERNS:
        return None
    P = len(pats)
    A = np.array(pats, dtype=float).T            # (tipi di pezzo) x (schemi)
    # Fase 1
    r1 = milp(c=np.ones(P), constraints=LinearConstraint(A, lb=d, ub=np.inf),
              integrality=np.ones(P), bounds=Bounds(0, np.inf),
              options={'time_limit': 6})
    if r1.x is None:
        return None
    x = np.round(r1.x).astype(int)
    n_star = int(x.sum())
    # Fase 2: meno schemi diversi, stesso numero di aste, pochi pezzi in eccesso
    eps = 1e-3
    c = np.concatenate([eps * A.sum(axis=0), np.ones(P)])
    A2 = np.hstack([A, np.zeros_like(A)])
    link = np.hstack([np.eye(P), -n_star * np.eye(P)])      # x_p <= n_star * y_p
    cons = [LinearConstraint(A2, lb=d, ub=np.inf),
            LinearConstraint(np.concatenate([np.ones(P), np.zeros(P)]).reshape(1, -1), lb=n_star, ub=n_star),
            LinearConstraint(link, lb=-np.inf, ub=0)]
    r2 = milp(c=c, constraints=cons, integrality=np.ones(2 * P),
              bounds=Bounds(0, np.concatenate([np.full(P, n_star), np.ones(P)])),
              options={'time_limit': 3})
    if r2.x is not None:
        x2 = np.round(r2.x[:P]).astype(int)
        if x2.sum() == n_star and (A @ x2 >= d - 1e-9).all():
            x = x2
    # costruzione aste
    bars = []
    for pat, q in zip(pats, x):
        for _ in range(int(q)):
            b = []
            for j, n in enumerate(pat):
                b += [lengths[j]] * n
            bars.append(b)
    # tolgo i pezzi in eccesso dalle aste dei gruppi più piccoli (così si creano meno schemi nuovi)
    for j, l in enumerate(lengths):
        surplus = sum(b.count(l) for b in bars) - cnt[l]
        if surplus > 0:
            gsize = Counter(tuple(sorted(b)) for b in bars)
            cand = sorted([b for b in bars if l in b], key=lambda b: gsize[tuple(sorted(b))])
            for b in cand[:surplus]:
                b.remove(l)
    bars = [b for b in bars if b]
    # controllo finale: stessi pezzi richiesti, tutte le aste valide
    got = Counter(l for b in bars for l in b)
    if got != cnt or not all(_fits(sum(b), len(b), kerf) for b in bars):
        return None
    return bars

def optimize_bars(pieces, kerf):
    """Prima ILP (ottimo), poi ricerca esatta DP, poi FFD come ultima riserva."""
    try:
        res = _optimize_ilp(pieces, kerf)
        if res:
            return res
    except Exception:
        pass
    return _optimize_dp(pieces, kerf)

@app.route('/api/cut', methods=['POST'])
def api_cut():
    data = request.get_json() or {}
    items = data.get('items', [])
    kerf = float(data.get('kerf', 1))

    con = get_db()
    pieces = []
    for item in items:
        pid = item['profile_id']
        qty = item['quantity']
        row = con.execute('SELECT * FROM profiles WHERE id=?', (pid,)).fetchone()
        if not row: continue
        profile = profile_dict(con, row)
        if profile['tipo'] != 'tappi':
            continue
        for p in profile['pieces_detail']:
            for _ in range(p['quantity'] * qty):
                # il colore dei tappi non conta per il taglio: le aste sono tutte uguali
                pieces.append((p['length_mm'], f"cp{profile['cp_num']:02d} {profile['tipo']}"))

    if not pieces:
        return jsonify({'bars': [], 'total_bars': 0, 'total_scrap': 0})

    # raggruppa le descrizioni per lunghezza, poi ottimizza solo sulle lunghezze
    pool = {}
    for length, desc in pieces:
        pool.setdefault(length, []).append(desc)
    bars_len = optimize_bars([l for l, _ in pieces], kerf)

    bars = []
    for bl in bars_len:
        bar_pieces = [(l, pool[l].pop()) for l in bl]
        used = sum(bl) + max(0, len(bl) - 1) * kerf
        bars.append({'pieces': bar_pieces, 'used': used})

    out = []
    for bar in bars:
        cnt = Counter(f"{desc} ({length}mm)" for length, desc in bar['pieces'])
        comp = sorted(f"{v}× {k}" for k, v in cnt.items())
        out.append({'pieces': comp, 'used': round(bar['used'], 1),
                    'scrap': round(BAR_LENGTH - bar['used'], 1)})

    # raggruppa le aste con lo stesso schema di taglio (le più numerose per prime)
    grouped = {}
    for b in out:
        grouped.setdefault((tuple(b['pieces']), b['used']), []).append(b)
    order = sorted(grouped.values(), key=lambda g: (-len(g), -g[0]['used']))

    out_bars, groups, idx = [], [], 1
    for g in order:
        first = idx
        for b in g:
            out_bars.append({'index': idx, 'pieces': b['pieces'], 'used': b['used'], 'scrap': b['scrap']})
            idx += 1
        groups.append({'first': first, 'last': idx - 1, 'count': len(g),
                       'pieces': g[0]['pieces'], 'used': g[0]['used'], 'scrap': g[0]['scrap']})
    total_scrap = sum(b['scrap'] for b in out_bars)

    return jsonify({
        'bars': out_bars,
        'groups': groups,
        'total_bars': len(bars),
        'total_scrap': round(total_scrap, 1),
        'kerf': kerf
    })

@app.route('/')
def index():
    return send_from_directory('/app', 'index.html')

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
