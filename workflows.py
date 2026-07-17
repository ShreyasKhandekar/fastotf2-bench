"""Queue-monitoring helpers for the FastOTF2 benchmark notebook.

A trimmed copy of the scaling workflow's workflows.py -- just the live squeue
monitor widget and the blocking wait, which are the only pieces the benchmark
uses. Keeping them here (rather than importing the scaling repo) makes this repo
self-contained.
"""
from IPython.display import display
from ipywidgets import HBox, Layout, Box
import ipywidgets as widgets
import shlex
import subprocess
import threading
import time

# Module-level so the widget's Start/Stop buttons share one background thread.
stop_squeue = threading.Event()
squeue_thread = None


def run_cmd(cmd):
    args = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    print(f"> {cmd}")
    subprocess.run(args, check=True)


def watch_queue_widget(interval=2.0):
    """Live queue monitor -- richer than `watch squeue --me`.

    Jobs are sorted (RUNNING first, longest-running on top; then PENDING by estimated start
    time; then everything else) and rendered as a scrollable, colour-coded table:
      * RUNNING  -> time USED, walltime LIMIT, and time LEFT (green)
      * PENDING  -> estimated START clock + a live countdown, plus the scheduler REASON (amber)
    A summary header shows counts, nodes in use, and the next estimated start. `interval` is
    the refresh period in seconds (also changeable live via the dropdown)."""
    import html as _html
    from datetime import datetime as _dt

    def _ts(s):
        try:
            return _dt.strptime(s, "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None

    def _dur_to_s(t):
        if not t or t in ("UNLIMITED", "INVALID", "NOT_SET", "N/A", ""):
            return None
        days = 0
        if "-" in t:
            d, t = t.split("-", 1)
            days = int(d)
        p = [int(x) for x in t.split(":")]
        while len(p) < 3:
            p.insert(0, 0)
        return days * 86400 + p[-3] * 3600 + p[-2] * 60 + p[-1]

    def _fmt_s(secs):
        if secs is None:
            return "?"
        neg = secs < 0
        secs = abs(int(secs))
        d, r = divmod(secs, 86400)
        h, r = divmod(r, 3600)
        m, s = divmod(r, 60)
        out = f"{d}d{h}h" if d else (f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s")
        return ("-" + out) if neg else out

    def _collect():
        fmt = "%i|%j|%T|%M|%l|%D|%Q|%r|%S"
        cmd = ["squeue", "-h", "-o", fmt] + (["--me"] if cb_just_me.value else [])
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return None, (res.stderr.strip() or f"rc={res.returncode}")
        rows = []
        for line in res.stdout.splitlines():
            f = line.split("|")
            if len(f) < 9:
                continue
            rows.append(dict(jid=f[0], name=f[1], st=f[2], elapsed=f[3], tlimit=f[4],
                             nodes=f[5], prio=f[6], reason=f[7], start=f[8]))
        return rows, None

    def _render():
        rows, err = _collect()
        now = _dt.now()
        stamp = now.strftime("%H:%M:%S")
        scope = "my" if cb_just_me.value else "all"
        BG, FG, MUTE, ACC = "#12141c", "#d6d9e0", "#8b93a7", "#9ecbff"
        RUN_C, WAIT_C, OTHER_C = "#4caf50", "#ffb74d", "#9e9e9e"

        def _wrap(inner):
            return (f"<div style='font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
                    f"font-size:12px;background:{BG};color:{FG};padding:10px;border-radius:6px;"
                    f"max-height:480px;overflow:auto'>{inner}</div>")

        if err is not None:
            return _wrap(f"<span style='color:#ff6b6b'>squeue error: {_html.escape(err)}</span>")

        def _tbl(title, tcolor, headers, rowvals):
            if not rowvals:
                return ""
            th = "".join(f"<th style='text-align:left;padding:3px 14px 3px 0;color:{MUTE};"
                         f"font-weight:normal;border-bottom:1px solid #2c3040'>{h}</th>"
                         for h in headers)
            body = ""
            for cells in rowvals:
                tds = "".join(f"<td style='padding:3px 14px 3px 0;white-space:nowrap'>"
                              f"{_html.escape(str(c))}</td>" for c in cells)
                body += f"<tr>{tds}</tr>"
            dot = f"<span style='color:{tcolor}'>&#9679;</span> "
            return (f"<div style='margin:10px 0 3px;font-weight:bold;color:{tcolor}'>{dot}{title}</div>"
                    f"<table style='border-collapse:collapse'><tr>{th}</tr>{body}</table>")

        run = [r for r in rows if r["st"] == "RUNNING"]
        pend = [r for r in rows if r["st"] == "PENDING"]
        other = [r for r in rows if r["st"] not in ("RUNNING", "PENDING")]

        # RUNNING: longest-running first.
        run.sort(key=lambda r: (_ts(r["start"]).timestamp() if _ts(r["start"]) else 0.0))
        run_rows = []
        for r in run:
            es, ls = _dur_to_s(r["elapsed"]), _dur_to_s(r["tlimit"])
            left = _fmt_s(ls - es) if (es is not None and ls is not None) else "?"
            run_rows.append([r["jid"], r["name"][:28], r["nodes"], r["elapsed"], r["tlimit"], left])

        # WAITING: highest priority first (that's the order they'll actually start).
        pend.sort(key=lambda r: -int(r["prio"]) if r["prio"].isdigit() else 0)
        wait_rows = []
        for r in pend:
            t = _ts(r["start"])
            when = t.strftime("%m-%d %H:%M") if t else "unknown"
            countdown = _fmt_s((t - now).total_seconds()) if t else "—"
            wait_rows.append([r["jid"], r["name"][:28], r["nodes"], r["prio"],
                              when, countdown, r["reason"]])

        other.sort(key=lambda r: r["st"])
        other_rows = [[r["jid"], r["name"][:28], r["st"], r["nodes"], r["reason"]] for r in other]

        run_nodes = sum(int(r["nodes"]) for r in run if r["nodes"].isdigit())
        pend_starts = [t for t in (_ts(r["start"]) for r in pend) if t]
        nxt = min(pend_starts).strftime("%m-%d %H:%M") if pend_starts else "n/a (deps/limits)"
        summary = (f"<span style='color:{ACC};font-weight:bold'>{stamp}</span> "
                   f"<span style='color:{MUTE}'>({scope} jobs)</span> &nbsp; "
                   f"<span style='color:{RUN_C}'>&#9679; {len(run)} running</span> "
                   f"<span style='color:{MUTE}'>({run_nodes} nodes)</span> &nbsp; "
                   f"<span style='color:{WAIT_C}'>&#9679; {len(pend)} waiting</span> &nbsp; "
                   f"<span style='color:{MUTE}'>next start: {nxt}</span>"
                   + ("  <b style='color:#ff6b6b'>[STOPPED]</b>" if stop_squeue.is_set() else ""))

        if not rows:
            return _wrap(summary + "<div style='margin-top:8px;color:#8b93a7'>(no jobs in queue)</div>")

        parts = [summary]
        parts.append(_tbl("RUNNING now — newest at bottom, time left to walltime", RUN_C,
                          ["job id", "name", "nodes", "elapsed", "walltime", "time left"], run_rows))
        parts.append(_tbl("WAITING to start — highest priority first (order they'll run)", WAIT_C,
                          ["job id", "name", "nodes", "priority", "est. start", "starts in",
                           "waiting on"], wait_rows))
        parts.append(_tbl("Other states", OTHER_C,
                          ["job id", "name", "state", "nodes", "reason"], other_rows))
        return _wrap("".join(parts))

    def squeue_update_thread():
        while not stop_squeue.is_set():
            out.value = _render()
            time.sleep(max(0.5, float(dd_interval.value)))
        out.value = _render()

    def on_btn_start(b):
        global squeue_thread
        stop_squeue.clear()
        if squeue_thread is None or not squeue_thread.is_alive():
            squeue_thread = threading.Thread(target=squeue_update_thread, daemon=True)
            squeue_thread.start()

    def on_force_refresh(b):
        out.value = _render()

    def on_btn_stop(b):
        stop_squeue.set()

    def on_btn_force_kill(b):
        run_cmd('scancel --me')

    stop_squeue.set()

    btn_start      = widgets.Button(description='Start', button_style='primary', icon='play')
    btn_stop       = widgets.Button(description='Stop',  button_style='danger', icon='stop')
    btn_refresh    = widgets.Button(description='Force Update', button_style='info', icon='refresh')
    btn_force_kill = widgets.Button(description='Force Kill', button_style='danger', icon='eject')
    cb_just_me     = widgets.Checkbox(description='My jobs only', value=True, disabled=False)
    dd_interval    = widgets.Dropdown(options=[1, 2, 5, 10], value=int(interval) if int(interval) in (1, 2, 5, 10) else 2,
                                      description='every (s)', style={'description_width': 'initial'},
                                      layout=Layout(width='140px'))

    btn_start.on_click(on_btn_start)
    btn_stop.on_click(on_btn_stop)
    btn_refresh.on_click(on_force_refresh)
    btn_force_kill.on_click(on_btn_force_kill)
    cb_just_me.observe(lambda ch: on_force_refresh(None) if ch['name'] == 'value' else None)

    controls = HBox(
        [btn_start, btn_stop, btn_refresh, dd_interval, Box(layout=Layout(flex='1 1 auto')),
         cb_just_me, btn_force_kill],
        layout=Layout(display='flex', flex_flow='row', align_items='center', gap='8px'))
    out = widgets.HTML(value="<div style='font-family:monospace'>Idle — press Start</div>")
    display(controls, out)


def wait_until_my_jobs_finished():
    wait = True
    while wait:
        # check queue length
        result = subprocess.run('squeue --me | wc -l', shell=True, capture_output=True, text=True)
        queue_length = int(result.stdout.strip())
        if queue_length == 1:  # there's always the header line
            wait = False
        else:
            time.sleep(5)
