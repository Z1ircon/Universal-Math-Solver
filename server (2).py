import socket
import threading
import multiprocessing
import sqlite3
import datetime
import os
import io
import base64
import json
import urllib.request
import urllib.error
import sympy as sp
from functools import lru_cache

# Headless plotting - the server has no screen, so Matplotlib must be told
# to render straight to an image buffer instead of trying to open a window.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HOST = '0.0.0.0'
PORT = 65432
NUM_WORKERS = 4  #currently hard locked at 4 worker processes as im worried about how it will affect my laptop will be increased later on
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history.db')

# Ollama runs locally alongside this server (same laptop), so it's reached
# over localhost - no network setup needed beyond having `ollama serve` running.
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma4:31b-cloud"  # a "-cloud" tag runs on Ollama's servers, not this laptop -
                                    # requires internet + `ollama signin`, but still answers on
                                    # localhost:11434 same as a fully local model would
OLLAMA_TIMEOUT = 30  # seconds - small local models can take a while on CPU

# Shared math symbols that clients can use in their expressions
x, y, z, t = sp.symbols('x y z t')
f = sp.Function('f')
PARSE_LOCALS = {'x': x, 'y': y, 'z': z, 't': t, 'f': f}
# Every calculation is saved to history.db so nothing is lost when the server restarts. A Lock is used because many client threads write at
# the same time, and SQLite does not like simultaneous writes.
# the database of history.db is saved as well in the server which in this case is my laptop
DB_LOCK = threading.Lock()
def init_db():
    """Create the history table once, if it does not exist yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            client_addr TEXT NOT NULL,
            operation   TEXT NOT NULL,
            input_expr  TEXT NOT NULL,
            result      TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_history(client_addr, operation, input_expr, result):
    """Save one calculation into the database (thread-safe)."""
    timestamp = datetime.datetime.now().isoformat(timespec='seconds')
    with DB_LOCK:                      # only one thread may write at a time
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO history (timestamp, client_addr, operation, input_expr, result) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, client_addr, operation, input_expr, result)
        )
        conn.commit()
        conn.close()


def get_recent_history(limit=20):
    """Read the newest 'limit' rows from the history table."""
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT timestamp, client_addr, operation, input_expr, result "
            "FROM history ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
    return rows



# MATH LAYER
# One small function per operation. Each function takes a plain string,
# turns it into a SymPy expression, does ONE math step, and returns it.


def parse_expression(expr_str):
    """Convert a text like 'x**2 + 3*x' into a SymPy expression."""
    return sp.sympify(expr_str, locals = PARSE_LOCALS)


def solve_equation(expr_str):
    return sp.solve(parse_expression(expr_str))


def differentiate(expr_str, var='x'):
    return sp.diff(parse_expression(expr_str), sp.symbols(var))


def integrate_expr(expr_str, var='x'):
    return sp.integrate(parse_expression(expr_str), sp.symbols(var))


def simplify_expr(expr_str):
    return sp.simplify(parse_expression(expr_str))


def factor_expr(expr_str):
    return sp.factor(parse_expression(expr_str))


def expand_expr(expr_str):
    return sp.expand(parse_expression(expr_str))


def apart_expr(expr_str):
    """Partial fraction decomposition."""
    return sp.apart(parse_expression(expr_str))


def trigsimp_expr(expr_str):
    return sp.trigsimp(parse_expression(expr_str))


def limit_expr(expr_str):
    """Format: 'expr@point'  e.g. 'sin(x)/x@0'"""
    expr_part, point_part = expr_str.split('@')
    return sp.limit(parse_expression(expr_part), x, parse_expression(point_part))


def series_expr(expr_str):
    """Format: 'expr@point,order'  e.g. 'exp(x)@0,4'  (order defaults to 4)"""
    if '@' in expr_str:
        expr_part, rest = expr_str.split('@')
        point, order = (rest.split(',') + ['4'])[:2]
    else:
        expr_part, point, order = expr_str, '0', '4'
    return sp.series(parse_expression(expr_part), x, parse_expression(point), int(order))


def dsolve_expr(expr_str):
    """Solve an ODE. Format: 'Derivative(f(x), x) - f(x)'"""
    return sp.dsolve(parse_expression(expr_str), f(x))


def gcd_expr(expr_str):
    """Format: 'expr1,expr2'"""
    e1, e2 = expr_str.split(',')
    return sp.gcd(parse_expression(e1), parse_expression(e2))


def lcm_expr(expr_str):
    """Format: 'expr1,expr2'"""
    e1, e2 = expr_str.split(',')
    return sp.lcm(parse_expression(e1), parse_expression(e2))


def nsolve_expr(expr_str):
    """Numeric root-finding. Format: 'expr,guess'  e.g. 'x**2 - 2,1'"""
    expr_part, guess = expr_str.split(',')
    return sp.nsolve(parse_expression(expr_part), x, float(guess))


def matrix_det(expr_str):
    """Format: '[[1,2],[3,4]]'"""
    m = sp.Matrix(sp.sympify(expr_str))
    return m.det()


def matrix_inverse(expr_str):
    """Format: '[[1,2],[3,4]]'"""
    m = sp.Matrix(sp.sympify(expr_str))
    return m.inv()


def normal_mean(expr_str):
    """Format: 'mu,sigma'  -> mean of a Normal distribution"""
    from sympy.stats import Normal, E
    mu, sigma = expr_str.split(',')
    Z = Normal('Z', sp.Float(mu), sp.Float(sigma))
    return E(Z)


def normal_variance(expr_str):
    """Format: 'mu,sigma'  -> variance of a Normal distribution"""
    from sympy.stats import Normal, variance
    mu, sigma = expr_str.split(',')
    Z = Normal('Z', sp.Float(mu), sp.Float(sigma))
    return variance(Z)


def normal_probability(expr_str):
    """Format: 'mu,sigma,threshold' -> P(Z > threshold)"""
    from sympy.stats import Normal, P
    mu, sigma, threshold = expr_str.split(',')
    Z = Normal('Z', sp.Float(mu), sp.Float(sigma))
    return P(Z > sp.Float(threshold)).evalf()


def plot_expr(expr_str):
    """
    Graphing calculator. Format: 'expr@xmin,xmax'  e.g. 'sin(x)@-10,10'

    Unlike the other functions, this doesn't return a SymPy value - it
    builds an actual PNG image on the server (using Matplotlib) and
    returns it as base64 text, tagged with an "IMAGE:" prefix so the
    client knows to render it as a picture instead of printing it.
    """
    expr_part, range_part = expr_str.split('@')
    xmin_str, xmax_str = range_part.split(',')
    xmin, xmax = float(xmin_str), float(xmax_str)

    expr = parse_expression(expr_part)
    # lambdify turns the symbolic expression into a fast numeric function
    # that numpy can evaluate at thousands of points at once
    numeric_func = sp.lambdify(x, expr, modules=['numpy'])

    xs = np.linspace(xmin, xmax, 400)
    with np.errstate(all='ignore'):   # e.g. tan(x) has asymptotes - don't crash, just show gaps
        ys = numeric_func(xs)
    ys = np.array(ys, dtype=float)
    ys[np.abs(ys) > 1e6] = np.nan     # hide points that blow up to infinity

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(xs, ys, color='#2563eb', linewidth=2)
    ax.axhline(0, color='gray', linewidth=0.8)
    ax.axvline(0, color='gray', linewidth=0.8)
    ax.set_title(f"y = {expr_part}")
    ax.grid(True, alpha=0.3)

    buffer = io.BytesIO()
    fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)   # free memory - important since this runs inside a worker process
    buffer.seek(0)

    img_base64 = base64.b64encode(buffer.read()).decode('ascii')
    return "IMAGE:" + img_base64



# OPERATION MENU
# Instead of a long if/elif chain, we keep a simple dictionary:
# the client's word ("diff", "solve", ...) points to the right function.


# ----------------------------------------------------------------------
# AI AGENT LAYER (local Ollama)
#
# Design: the LLM NEVER does the actual arithmetic itself - language
# models are unreliable calculators. Instead it's used for two things
# SymPy can't do: (1) understanding a plain-English question well enough
# to pick the right operation + expression, and (2) explaining the exact
# SymPy result back in plain English. The math in the middle is always
# done by the real symbolic engine, via OPERATIONS below - ai_solve just
# reads from that dict by name at call time, so it's fine that this
# function is defined before OPERATIONS itself exists yet.
# ----------------------------------------------------------------------

def ollama_generate(prompt, model=OLLAMA_MODEL, timeout=OLLAMA_TIMEOUT):
    """Sends one prompt to the local Ollama server and returns its text
    reply. Uses only the standard library (urllib) so no extra pip
    package is required just to talk to Ollama's local REST API."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Could not reach Ollama at {OLLAMA_URL}. "
            f"Is 'ollama serve' running? ({e})"
        )


_AI_ROUTER_PROMPT = """You are a router for a math server. Translate the question into EXACTLY one line in this format:
operation|expression

Valid operations: solve, diff, integrate, simplify, factor, expand, apart, trigsimp, limit, series, dsolve, gcd, lcm, nsolve, det, inverse, mean, variance, probability

Expression must be valid SymPy syntax using x as the variable (** for powers, no spaces around operators where avoidable).
Do not include any explanation, markdown, or extra text - output ONLY the one line.

Examples:
Question: what are the roots of x squared minus four
solve|x**2-4

Question: find the derivative of x cubed plus 2x
diff|x**3+2*x

Question: {question}
"""

_AI_EXPLAIN_PROMPT = """Explain this result in 1-2 short, simple sentences for a student. Do not repeat the raw expression syntax, describe it in words.

Question: {question}
We computed: {operation}({expression}) = {result}

Explanation:"""


def ai_solve(question_str):
    """
    'ai' operation - takes a natural-language math question.
    Step 1: ask the LLM to translate it into our operation|expression protocol.
    Step 2: run that through the REAL SymPy engine (exact, not AI-guessed).
    Step 3: ask the LLM to explain the exact result in plain English.
    """
    try:
        routed = ollama_generate(_AI_ROUTER_PROMPT.format(question=question_str))
    except ConnectionError as e:
        # safe_apply() would otherwise swallow this into a vague "unsupported
        # operation" message - catch it here so the real reason reaches the client.
        return str(e)
    line = routed.strip().splitlines()[0].strip() if routed.strip() else ""

    if '|' not in line:
        return f"AI could not translate this into a solvable expression.\nRaw AI reply: {routed}"

    operation, expression = line.split('|', 1)
    operation, expression = operation.strip(), expression.strip()

    func = OPERATIONS.get(operation)
    if not func:
        return f"AI suggested unsupported operation '{operation}'.\nRaw AI reply: {routed}"

    result = safe_apply(func, expression)
    if result is None:
        return f"AI interpreted this as {operation}({expression}), but that failed to compute."

    try:
        explanation = ollama_generate(_AI_EXPLAIN_PROMPT.format(
            question=question_str, operation=operation, expression=expression, result=result
        ))
    except ConnectionError:
        # The math itself succeeded even though the follow-up explanation call
        # failed - still return the exact result rather than losing it.
        explanation = "(AI explanation unavailable - Ollama connection lost)"

    return (
        f"Interpreted as: {operation}({expression})\n"
        f"Exact result (SymPy): {result}\n"
        f"Explanation: {explanation}"
    )


OPERATIONS = {
    'solve':       solve_equation,
    'diff':        differentiate,
    'integrate':   integrate_expr,
    'simplify':    simplify_expr,
    'factor':      factor_expr,
    'expand':      expand_expr,
    'apart':       apart_expr,
    'trigsimp':    trigsimp_expr,
    'limit':       limit_expr,
    'series':      series_expr,
    'dsolve':      dsolve_expr,
    'gcd':         gcd_expr,
    'lcm':         lcm_expr,
    'nsolve':      nsolve_expr,
    'det':         matrix_det,
    'inverse':     matrix_inverse,
    'mean':        normal_mean,
    'variance':    normal_variance,
    'probability': normal_probability,
    'plot':        plot_expr,
    'ai':          ai_solve,
}


def safe_apply(func, expr):
    """Try one expression; if it fails, return None instead of crashing."""
    try:
        return func(expr)
    except Exception:
        return None


def compute_batch(expr_list, operation):
    """Run the operation on every expression.
    Returns (good_results, error_messages) so the caller can tell the user
    WHY something failed instead of showing one vague message."""
    func = OPERATIONS.get(operation)   # find the right math function
    if not func:
        return [], [f"Unknown operation '{operation}'. Server knows: " + ", ".join(OPERATIONS)]

    valid_results = []
    errors = []
    for expr in expr_list:
        try:
            result = func(expr)
            if result is not None:
                valid_results.append(result)
        except Exception as e:         # keep going, but remember what went wrong
            errors.append(f"{operation}({expr}) failed: {type(e).__name__}: {e}")
    return valid_results, errors


@lru_cache(maxsize=2048)
def process_request(data):
    """
    Protocol: "operation|expr1;expr2;expr3"
    e.g. "diff|x**2+3*x;sin(x)"

    lru_cache remembers recent answers, so if the SAME request comes in
    again, the answer is returned instantly without doing the math again.
    """
    try:
        operation, exprs_raw = data.split('|', 1)
        operation = operation.strip()

        expr_list = []
        for e in exprs_raw.split(';'):     # "a;b;c" -> ['a', 'b', 'c']
            e = e.strip()
            if e:
                expr_list.append(e)

        results, errors = compute_batch(expr_list, operation)

        formatted = ""
        for r in results:
            formatted += str(r) + "\n"
        for err in errors:
            formatted += "Error: " + err + "\n"

        if formatted:
            return formatted
        return "No valid result (empty input?)"
    except Exception as e:
        return f"Error: {e}"



# SOCKET + MULTIPROCESSING LAYER
# The idea in one sentence:
#   - a THREAD per client  -> cheap, handles only sending/receiving text
#   - a POOL of PROCESSES  -> 4 permanent workers that do the actual math on separate CPU cores, reused forever (no process is created per connection, which would be very slow).


WORKER_POOL = None   # created once in __main__


def recv_message(conn):
    """
    Read one complete message from the socket.

    Sending rule: first 4 bytes = message length, then the message itself.
    Reading rule: read the 4 bytes, then keep reading until we have
    exactly that many bytes. This prevents broken/partial messages.
    """
    header = conn.recv(4)
    if not header:
        return ""

    length = int.from_bytes(header, 'big')

    chunks = []
    remaining = length
    while remaining > 0:
        chunk = conn.recv(min(4096, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks).decode('utf-8')


def send_message(conn, text):
    """Send text as: 4-byte length header + the message itself."""
    payload = text.encode('utf-8')
    conn.sendall(len(payload).to_bytes(4, 'big') + payload)


def format_history(rows):
    """Turn database rows into readable text for the client."""
    if not rows:
        return "No history yet."
    lines = []
    for ts, addr, op, expr, res in rows:
        lines.append(f"[{ts}] {op}({expr}) -> {res}")
    return "\n".join(lines)


def handle_client(conn, addr):
    """Runs in a THREAD: talk to one client until it disconnects."""
    print(f"[THREAD] Serving {addr} | thread={threading.current_thread().name}")
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send small messages immediately
    client_id = f"{addr[0]}:{addr[1]}"

    with conn:
        while True:
            message = recv_message(conn)
            if not message or message.strip().lower() == 'exit':
                break

            # Special command: "history|N" -> send the last N stored calculations
            if message.startswith('history'):
                _, _, count = message.partition('|')
                limit = int(count) if count.strip().isdigit() else 20
                response = format_history(get_recent_history(limit))
                send_message(conn, response)
                continue

            # Normal request -> give it to a pool worker for the math
            response = WORKER_POOL.apply(process_request, (message,))
            send_message(conn, response)

            # Save this calculation to the database
            operation, _, expr = message.partition('|')
            operation = operation.strip()
            # Don't store the whole base64 image blob in the history table -
            # it would bloat the database and make "history" unreadable.
            logged_result = "[plot image]" if operation == 'plot' else response.strip()
            log_history(client_id, operation, expr.strip(), logged_result)

    print(f"[CLOSED] {addr} disconnected")


def get_lan_ip():
    """Find this machine's LAN IP so other devices know where to connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))   # no data is actually sent; this just picks the right interface
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def start_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen()
        lan_ip = get_lan_ip()
        print("Smart Math Server is running.")
        print(f"  -> On THIS machine, connect to:      127.0.0.1:{PORT}")
        print(f"  -> From ANOTHER device on same WiFi:  {lan_ip}:{PORT}")
        print(f"Worker processes: {NUM_WORKERS}")

        while True:
            conn, addr = s.accept()
            # Spawn one cheap thread per client, then loop back to accept the next
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()


if __name__ == "__main__":
    init_db()                                                  # create history.db if missing
    WORKER_POOL = multiprocessing.Pool(processes=NUM_WORKERS)  # create 4 workers ONCE
    start_server()