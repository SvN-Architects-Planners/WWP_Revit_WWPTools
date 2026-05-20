import json
import os
import tempfile

try:
    import WWP_uiUtils as ui
    _has_ui = True
except Exception:
    _has_ui = False


def _appdata_root():
    root = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming"
    )
    return os.path.join(root, "pyRevit", "WWPTools")


def _load_events():
    archive_dir = os.path.join(_appdata_root(), "telemetry", "telemetry-archive")
    if not os.path.isdir(archive_dir):
        return []
    events = []
    for fname in os.listdir(archive_dir):
        if not fname.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(archive_dir, fname), "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
    return events


def _week_start(timestamp_utc):
    try:
        from datetime import datetime, timedelta
        text = str(timestamp_utc or "").strip().replace("Z", "")
        dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        return (dt - timedelta(days=dt.weekday())).date().isoformat()
    except Exception:
        return "unknown"


def _escape(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_html(events):
    from datetime import datetime

    cmd_events = [e for e in events if e.get("event_type") == "command-exec"]

    tool_counts = {}
    weekly_runs = {}
    weekly_tools = {}
    for e in cmd_events:
        name = str(e.get("command_name") or e.get("tool_key") or "(unknown)").strip() or "(unknown)"
        tool_counts[name] = tool_counts.get(name, 0) + 1
        week = _week_start(e.get("timestamp_utc"))
        weekly_runs[week] = weekly_runs.get(week, 0) + 1
        weekly_tools.setdefault(week, set()).add(name)

    versions = [str(e.get("extension_version") or "").strip() for e in events if e.get("extension_version")]
    latest_version = max(versions) if versions else "-"

    sorted_tools = sorted(tool_counts.items(), key=lambda x: -x[1])
    sorted_weeks = sorted(weekly_runs.keys(), reverse=True)

    tool_rows = "".join(
        "<tr><td>%s</td><td class='num'>%d</td></tr>" % (_escape(n), c)
        for n, c in sorted_tools
    ) or "<tr><td colspan='2' class='empty'>No tool usage recorded yet.</td></tr>"

    weekly_rows = "".join(
        "<tr><td>%s</td><td class='num'>%d</td><td class='num'>%d</td></tr>" % (
            _escape(w), weekly_runs[w], len(weekly_tools.get(w, set()))
        )
        for w in sorted_weeks
    ) or "<tr><td colspan='3' class='empty'>No weekly data recorded yet.</td></tr>"

    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>WWPTools Usage Report</title>
  <style>
    body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#1f2937;background:#f9fafb}
    h1{margin-bottom:4px}
    .sub{color:#6b7280;margin-bottom:28px;font-size:14px}
    .cards{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px}
    .card{background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;min-width:150px}
    .card-label{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em}
    .card-value{font-size:30px;font-weight:700;color:#111827;margin-top:6px}
    h2{margin:24px 0 8px;font-size:15px;color:#374151}
    table{border-collapse:collapse;width:100%%;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:28px}
    th{background:#f3f4f6;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;padding:10px 14px;text-align:left}
    td{padding:10px 14px;border-bottom:1px solid #f3f4f6;font-size:14px}
    tr:last-child td{border-bottom:none}
    .num{text-align:right;font-variant-numeric:tabular-nums}
    .empty{color:#9ca3af;font-style:italic;text-align:center}
  </style>
</head>
<body>
  <h1>WWPTools Usage Report</h1>
  <div class="sub">Your personal usage &middot; Local telemetry archive &middot; Generated %(generated)s</div>
  <div class="cards">
    <div class="card"><div class="card-label">Total runs</div><div class="card-value">%(total_runs)d</div></div>
    <div class="card"><div class="card-label">Unique tools</div><div class="card-value">%(unique_tools)d</div></div>
    <div class="card"><div class="card-label">Weeks of data</div><div class="card-value">%(weeks)d</div></div>
    <div class="card"><div class="card-label">Version</div><div class="card-value">%(version)s</div></div>
  </div>
  <h2>Tool Usage</h2>
  <table>
    <thead><tr><th>Tool</th><th class="num">Runs</th></tr></thead>
    <tbody>%(tool_rows)s</tbody>
  </table>
  <h2>Weekly Activity</h2>
  <table>
    <thead><tr><th>Week</th><th class="num">Runs</th><th class="num">Tools Used</th></tr></thead>
    <tbody>%(weekly_rows)s</tbody>
  </table>
</body>
</html>""" % {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "total_runs": len(cmd_events),
        "unique_tools": len(tool_counts),
        "weeks": len(weekly_runs),
        "version": _escape(latest_version),
        "tool_rows": tool_rows,
        "weekly_rows": weekly_rows,
    }


def main():
    events = _load_events()
    html = _build_html(events)
    report_path = os.path.join(tempfile.gettempdir(), "wwptools_usage_report.html")
    try:
        with open(report_path, "w") as f:
            f.write(html)
        os.startfile(report_path)
    except Exception as exc:
        if _has_ui:
            ui.uiUtils_alert("Could not open usage report:\n" + str(exc))


if __name__ == "__main__":
    main()
