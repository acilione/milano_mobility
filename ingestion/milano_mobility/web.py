"""Dependency-light visual dashboard for the published mobility marts."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg
import structlog
from psycopg.rows import dict_row

logger = structlog.get_logger()


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _database_parameters() -> dict[str, str | int]:
    return {
        "host": os.getenv("POSTGRES_HOST", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "mobility"),
        "user": os.getenv("BI_DB_USER", "bi_reader"),
        "password": os.getenv("BI_DB_PASSWORD", "bi_reader_dev"),
        "connect_timeout": 5,
    }


def load_dashboard_data() -> dict[str, Any]:
    """Read the published marts through the least-privilege BI account."""
    try:
        parameters = _database_parameters()
        with psycopg.connect(
            host=str(parameters["host"]),
            port=int(parameters["port"]),
            dbname=str(parameters["dbname"]),
            user=str(parameters["user"]),
            password=str(parameters["password"]),
            connect_timeout=int(parameters["connect_timeout"]),
            row_factory=dict_row,
        ) as connection:
            summary = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT max(snapshot_date) AS snapshot_date
                    FROM marts.dashboard_service_activity
                )
                SELECT
                    latest.snapshot_date,
                    sum(service.scheduled_trips) AS scheduled_trips,
                    count(DISTINCT service.service_date) AS service_days,
                    (
                        SELECT sum(stop.stop_events)
                        FROM marts.dashboard_stop_activity AS stop
                        WHERE stop.snapshot_date = latest.snapshot_date
                    ) AS stop_events,
                    (
                        SELECT count(DISTINCT stop.stop_sk)
                        FROM marts.dashboard_stop_activity AS stop
                        WHERE stop.snapshot_date = latest.snapshot_date
                    ) AS active_stops,
                    count(DISTINCT service.route_sk) AS active_routes
                FROM marts.dashboard_service_activity AS service
                CROSS JOIN latest_snapshot AS latest
                WHERE service.snapshot_date = latest.snapshot_date
                GROUP BY latest.snapshot_date
                """
            ).fetchone()
            departures = connection.execute(
                """
                SELECT
                    service_hour,
                    sum(scheduled_trips) AS departures
                FROM marts.dashboard_service_activity
                WHERE snapshot_date = (SELECT max(snapshot_date) FROM marts.dashboard_service_activity)
                  AND service_hour IS NOT NULL
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
            service_days = connection.execute(
                """
                SELECT
                    calendar.date AS service_date,
                    calendar.weekday_name,
                    calendar.is_weekend,
                    calendar.is_holiday,
                    weather.temperature_max_c,
                    weather.precipitation_mm,
                    coalesce(sum(service.scheduled_trips), 0) AS scheduled_trips
                FROM marts.dim_date AS calendar
                LEFT JOIN marts.dim_weather_day AS weather USING (date_key)
                LEFT JOIN marts.dashboard_service_activity AS service USING (date_key)
                WHERE calendar.date BETWEEN
                    (SELECT min(service_date) FROM marts.dashboard_service_activity)
                    AND (SELECT max(service_date) FROM marts.dashboard_service_activity)
                  AND service.snapshot_date = (
                      SELECT max(snapshot_date) FROM marts.dashboard_service_activity
                  )
                GROUP BY 1, 2, 3, 4, 5, 6
                ORDER BY 1
                """
            ).fetchall()
            route_coverage = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT max(snapshot_date) AS snapshot_date
                    FROM marts.dashboard_service_activity
                ),
                service_by_route AS (
                    SELECT route_sk, sum(scheduled_trips) AS trips
                    FROM marts.dashboard_service_activity AS service
                    CROSS JOIN latest_snapshot
                    WHERE service.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY route_sk
                ),
                stops_by_route AS (
                    SELECT
                        route_sk,
                        count(DISTINCT stop_sk) AS served_stops,
                        sum(stop_events) AS stop_events
                    FROM marts.dashboard_stop_activity AS stop
                    CROSS JOIN latest_snapshot
                    WHERE stop.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY route_sk
                )
                SELECT
                    CASE
                        WHEN route.route_type = 1 AND route.route_short_name LIKE 'M%'
                            THEN route.route_short_name
                        WHEN route.route_type = 1 THEN 'M' || route.route_short_name
                        ELSE coalesce(route.route_short_name, route.route_id)
                    END AS route_name,
                    route.route_long_name,
                    route.route_color,
                    route.route_type,
                    stops.served_stops,
                    service.trips,
                    stops.stop_events
                FROM service_by_route AS service
                JOIN stops_by_route AS stops USING (route_sk)
                JOIN marts.dim_route AS route USING (route_sk)
                ORDER BY route.route_type, 1
                """
            ).fetchall()
            changes = connection.execute(
                """
                SELECT entity_type, change_type, count(*) AS changed_entities
                FROM marts.fact_network_change
                WHERE snapshot_date = (SELECT max(snapshot_date) FROM marts.fact_network_change)
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            ).fetchall()
            stops = connection.execute(
                """
                SELECT
                    stop.stop_id,
                    stop.stop_name,
                    stop.stop_lat,
                    stop.stop_lon,
                    sum(activity.stop_events) AS stop_events
                FROM marts.dashboard_stop_activity AS activity
                JOIN marts.dim_stop AS stop USING (stop_sk)
                WHERE activity.snapshot_date = (
                    SELECT max(snapshot_date) FROM marts.dashboard_stop_activity
                )
                GROUP BY 1, 2, 3, 4
                ORDER BY stop_events DESC, stop.stop_name
                """
            ).fetchall()
        return {
            "state": "ready",
            "generated_at": datetime.now().astimezone(),
            "summary": summary or {},
            "departures": departures,
            "service_days": service_days,
            "route_coverage": route_coverage,
            "changes": changes,
            "stops": stops,
        }
    except (psycopg.Error, KeyError) as error:
        logger.info("dashboard_waiting_for_marts", reason=str(error).splitlines()[0])
        return {
            "state": "waiting",
            "generated_at": datetime.now().astimezone(),
            "message": "The dashboard is ready and waiting for the first published snapshot.",
        }


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the dashboard shell and its read-only JSON data."""

    server_version = "MilanoMobilityDashboard/1.0"

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send(HTTPStatus.OK, DASHBOARD_HTML, "text/html; charset=utf-8")
            return
        if self.path == "/api/dashboard":
            payload = json.dumps(load_dashboard_data(), default=_json_default).encode()
            self._send(HTTPStatus.OK, payload, "application/json")
            return
        if self.path == "/health":
            self._send(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
            return
        self._send(HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json")

    def log_message(self, message_format: str, *args: object) -> None:
        logger.info("dashboard_request", message=message_format % args)

    def _send(self, status: HTTPStatus, body: str | bytes, content_type: str) -> None:
        encoded = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Run the local dashboard server."""
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8501"))
    logger.info("dashboard_started", host=host, port=port)
    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="theme-color" content="#07110f">
  <title>Milano Mobility Observatory</title>
  <style>
    :root {
      --ink: #eef7f1; --muted: #91a49b; --panel: rgba(15, 31, 27, .84);
      --line: rgba(217, 240, 226, .12); --lime: #c8ff63; --mint: #48e3b5;
      --coral: #ff7a66; --sky: #68b5ff; --amber: #ffc45c; --bg: #07110f;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0; color: var(--ink); background:
        radial-gradient(circle at 8% 4%, rgba(72,227,181,.13), transparent 26rem),
        radial-gradient(circle at 95% 30%, rgba(104,181,255,.1), transparent 32rem),
        var(--bg);
      font: 15px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }
    body:before {
      content: ""; position: fixed; inset: 0; pointer-events: none; opacity: .2;
      background-image: linear-gradient(var(--line) 1px, transparent 1px),
        linear-gradient(90deg, var(--line) 1px, transparent 1px);
      background-size: 54px 54px; mask-image: linear-gradient(to bottom, black, transparent 80%);
    }
    .shell { width: min(1440px, calc(100% - 40px)); margin: auto; position: relative; }
    nav {
      height: 76px; display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid var(--line);
    }
    .brand { display: flex; align-items: center; gap: 13px; font-weight: 760; letter-spacing: -.02em; }
    .brand-mark { width: 34px; height: 34px; border: 1px solid var(--lime); border-radius: 50%; position: relative; }
    .brand-mark:before,.brand-mark:after { content:""; position:absolute; background:var(--lime); border-radius:9px; }
    .brand-mark:before { width: 4px; height: 19px; left: 8px; top: 7px; box-shadow: 7px 4px 0 var(--mint), 14px -2px 0 var(--sky); }
    .status { display: flex; align-items: center; gap: 9px; color: var(--muted); font-size: 13px; }
    .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); box-shadow: 0 0 0 5px rgba(255,196,92,.1); }
    .pulse.ready { background: var(--mint); box-shadow: 0 0 0 5px rgba(72,227,181,.1); }
    header { padding: 64px 0 40px; display: grid; grid-template-columns: 1.6fr .8fr; gap: 50px; align-items: end; }
    .eyebrow { color: var(--lime); font-size: 12px; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
    h1 { font-size: clamp(44px, 6vw, 86px); line-height: .97; letter-spacing: -.065em; max-width: 900px; margin: 16px 0 24px; font-weight: 770; }
    h1 em { color: var(--mint); font-style: normal; }
    .lede { max-width: 680px; color: var(--muted); font-size: 17px; }
    .snapshot { border-left: 1px solid var(--line); padding-left: 30px; }
    .snapshot span { display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.12em; }
    .snapshot strong { display:block; font-size:28px; margin:6px 0; letter-spacing:-.03em; }
    .snapshot small { color:var(--muted); }
    .kpis { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:12px; }
    .card,.panel {
      background: linear-gradient(145deg, rgba(18,38,32,.92), rgba(10,24,21,.84));
      border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 18px 60px rgba(0,0,0,.18);
    }
    .card { padding:20px; min-height:135px; display:flex; flex-direction:column; justify-content:space-between; }
    .card .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.09em; }
    .card .value { font-size:34px; letter-spacing:-.045em; font-weight:740; }
    .card .note { color:var(--muted); font-size:12px; }
    .dashboard-grid { display:grid; grid-template-columns:1.35fr .85fr; gap:12px; }
    .panel { padding:24px; min-height:330px; overflow:hidden; }
    .panel.wide { grid-column:1 / -1; }
    .panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:24px; }
    .panel h2 { font-size:18px; margin:0 0 5px; letter-spacing:-.02em; }
    .panel p { margin:0; color:var(--muted); font-size:13px; }
    .tag { color:var(--lime); border:1px solid rgba(200,255,99,.25); border-radius:99px; padding:5px 10px; font-size:11px; white-space:nowrap; }
    .chart { height:225px; display:flex; align-items:flex-end; gap:9px; border-bottom:1px solid var(--line); padding-top:18px; }
    .bar-wrap { flex:1; height:100%; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; min-width:18px; }
    .bar { width:100%; max-width:34px; min-height:3px; border-radius:6px 6px 1px 1px; background:linear-gradient(to top,var(--mint),var(--lime)); position:relative; transition:.35s ease; }
    .bar:hover { filter:brightness(1.15); transform:translateY(-2px); }
    .bar:hover:before { content:attr(data-value); position:absolute; top:-29px; left:50%; transform:translateX(-50%); background:#eef7f1; color:#07110f; border-radius:5px; padding:3px 6px; font-size:11px; font-weight:800; }
    .hour { font-size:10px; color:var(--muted); margin-top:8px; }
    #trend { width:100%; height:235px; overflow:visible; }
    .mode-summary { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .mode-summary span { color:var(--muted); border:1px solid var(--line); border-radius:99px; padding:5px 10px; font-size:11px; }
    .route-list { display:grid; gap:10px; max-height:490px; overflow-y:auto; padding-right:8px; scrollbar-color:var(--mint) transparent; }
    .route { display:grid; grid-template-columns:42px 1fr auto; gap:12px; align-items:center; padding:12px 0; border-bottom:1px solid var(--line); }
    .route:last-child { border:0; }
    .route-badge { width:39px; height:27px; display:grid; place-items:center; border-radius:7px; background:var(--lime); color:#07110f; font-weight:850; font-size:12px; }
    .route strong { display:block; font-size:13px; }.route small { color:var(--muted); }
    .route-metric { text-align:right; font-weight:760; }.route-metric small { display:block; font-weight:400; }
    .map { min-height:350px; position:relative; border:1px solid var(--line); border-radius:14px; overflow:hidden;
      background: radial-gradient(circle at 60% 30%,rgba(72,227,181,.12),transparent 35%),
      linear-gradient(135deg,rgba(255,255,255,.02),transparent); }
    .map:before { content:""; position:absolute; inset:0; opacity:.35; background-image:
      linear-gradient(28deg, transparent 48%, rgba(145,164,155,.14) 49%, transparent 50%),
      linear-gradient(118deg, transparent 48%, rgba(145,164,155,.12) 49%, transparent 50%);
      background-size:70px 70px; }
    #network-map { width:100%; height:350px; position:relative; z-index:1; }
    .change-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
    .change { padding:16px; border:1px solid var(--line); border-radius:12px; }
    .change .entity { color:var(--muted); text-transform:capitalize; font-size:12px; }
    .change strong { font-size:29px; display:block; margin:5px 0; }
    .change .split { display:flex; gap:8px; flex-wrap:wrap; font-size:10px; text-transform:uppercase; }
    .added{color:var(--mint)}.modified{color:var(--amber)}.removed{color:var(--coral)}
    .pipeline { display:grid; grid-template-columns:repeat(5,1fr); align-items:center; gap:16px; padding:18px 4px 2px; }
    .stage { position:relative; padding:16px; border:1px solid var(--line); border-radius:12px; min-height:100px; }
    .stage:not(:last-child):after { content:"→"; position:absolute; right:-14px; top:36%; color:var(--lime); font-size:18px; }
    .stage b { display:block; color:var(--lime); font-size:11px; letter-spacing:.08em; margin-bottom:7px; }
    .stage span { font-weight:700; }.stage small { display:block; color:var(--muted); margin-top:5px; }
    .waiting { grid-column:1/-1; padding:70px 30px; text-align:center; border:1px dashed rgba(200,255,99,.3); border-radius:18px; }
    .waiting-orbit { width:58px;height:58px;border:1px solid var(--line);border-top-color:var(--lime);border-radius:50%;margin:0 auto 22px;animation:spin 1.2s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    footer { padding:38px 0 50px; display:flex; justify-content:space-between; color:var(--muted); font-size:12px; }
    @media(max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}.dashboard-grid{grid-template-columns:1fr}.pipeline{grid-template-columns:1fr}.stage:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-18px}.panel.wide{grid-column:auto}}
    @media(max-width:680px){.shell{width:min(100% - 24px,1440px)}header{grid-template-columns:1fr;padding-top:40px}.snapshot{border-left:0;border-top:1px solid var(--line);padding:20px 0 0}.kpis{grid-template-columns:1fr 1fr}.card{min-height:115px}.panel{padding:18px}.change-grid{grid-template-columns:1fr}footer{display:block}.brand-copy{display:none}}
  </style>
</head>
<body>
  <div class="shell">
    <nav>
      <div class="brand"><div class="brand-mark"></div><span class="brand-copy">Milano Mobility Observatory</span></div>
      <div class="status"><i class="pulse" id="pulse"></i><span id="status">Connecting to published marts</span></div>
    </nav>
    <header>
      <div>
        <div class="eyebrow">Scheduled transport intelligence / Milan</div>
        <h1>See the network.<br><em>Read the rhythm.</em></h1>
        <div class="lede">A living view of Milan's complete official scheduled public-transport feed, historical network versions, service-day patterns, and quality-controlled data products.</div>
      </div>
      <div class="snapshot"><span>Source snapshot</span><strong id="snapshot">Preparing data</strong><small id="generated">The page refreshes automatically</small></div>
    </header>
    <main id="main">
      <section class="waiting"><div class="waiting-orbit"></div><h2>Building the first analytical view</h2><p>The dashboard will populate as soon as dbt publishes the marts.</p></section>
    </main>
    <footer><span>Official GTFS · Comune di Milano / AMAT · CC BY 4.0</span><span>Static GTFS measures scheduled supply, not real-time punctuality.</span></footer>
  </div>
  <script>
    const fmt = n => new Intl.NumberFormat("en-GB").format(Number(n || 0));
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const routeColor = r => {
      const metro={M1:"#e51b23",M2:"#009d58",M3:"#ffd500",M4:"#0072ce",M5:"#8a2be2"};
      if(metro[r.route_name])return metro[r.route_name];
      if(/^[0-9a-fA-F]{6}$/.test(r.route_color||""))return `#${r.route_color}`;
      return +r.route_type===0?"#ffc45c":+r.route_type===1?"#c8ff63":"#68b5ff";
    };
    function metric(label,value,note){return `<article class="card"><span class="label">${label}</span><strong class="value">${fmt(value)}</strong><span class="note">${note}</span></article>`}
    function hourly(rows){
      const max=Math.max(...rows.map(x=>+x.departures),1);
      return `<div class="chart">${rows.map(x=>`<div class="bar-wrap"><div class="bar" data-value="${fmt(x.departures)}" style="height:${Math.max(3,+x.departures/max*100)}%"></div><span class="hour">${String(x.service_hour).padStart(2,"0")}:00</span></div>`).join("")}</div>`;
    }
    function trend(rows){
      const width=760,height=215,pad=20,max=Math.max(...rows.map(x=>+x.scheduled_trips),1);
      const pts=rows.map((x,i)=>({x:pad+i*(width-pad*2)/Math.max(rows.length-1,1),y:height-pad-(+x.scheduled_trips/max)*(height-pad*2),v:+x.scheduled_trips,d:x.service_date}));
      const line=pts.map((p,i)=>`${i?"L":"M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
      const area=`${line} L${pts.at(-1)?.x||pad},${height-pad} L${pad},${height-pad} Z`;
      return `<svg id="trend" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#48e3b5" stop-opacity=".35"/><stop offset="1" stop-color="#48e3b5" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#fade)"/><path d="${line}" fill="none" stroke="#c8ff63" stroke-width="3" vector-effect="non-scaling-stroke"/>${pts.filter((_,i)=>i%Math.max(1,Math.ceil(pts.length/9))===0).map(p=>`<circle cx="${p.x}" cy="${p.y}" r="3" fill="#07110f" stroke="#c8ff63" stroke-width="2"><title>${p.d}: ${p.v} trips</title></circle>`).join("")}</svg>`;
    }
    function map(stops){
      if(!stops.length)return "<div class='waiting'>No geocoded stops are available.</div>";
      const lats=stops.map(x=>+x.stop_lat),lons=stops.map(x=>+x.stop_lon),minLat=Math.min(...lats),maxLat=Math.max(...lats),minLon=Math.min(...lons),maxLon=Math.max(...lons);
      const pt=(s,i)=>{const x=35+((+s.stop_lon-minLon)/Math.max(maxLon-minLon,.0001))*690,y=320-((+s.stop_lat-minLat)/Math.max(maxLat-minLat,.0001))*290,r=1.5+Math.min(5,Math.log10(+s.stop_events+1));return {x,y,r,s,i}};
      const points=stops.map(pt);
      const labels=points.slice(0,18);
      return `<div class="map"><svg id="network-map" viewBox="0 0 760 350">${points.map(p=>`<circle cx="${p.x}" cy="${p.y}" r="${p.r}" fill="#48e3b5" fill-opacity=".62" stroke="#07110f" stroke-width=".5"><title>${esc(p.s.stop_name)} · ${fmt(p.s.stop_events)} scheduled calls</title></circle>`).join("")}${labels.map(p=>`<g><circle cx="${p.x}" cy="${p.y}" r="${p.r+4}" fill="#c8ff63" opacity=".18"/><text x="${p.x+p.r+5}" y="${p.y+3}" fill="#eef7f1" font-size="9" font-weight="700">${esc(p.s.stop_name)}</text></g>`).join("")}</svg></div>`;
    }
    function routes(rows){
      const names={0:"tram",1:"metro",3:"bus / trolleybus"}, counts={}; rows.forEach(r=>counts[r.route_type]=(counts[r.route_type]||0)+1);
      const summary=Object.entries(counts).map(([type,count])=>`<span>${fmt(count)} ${names[type]||"other"} routes</span>`).join("");
      return `<div class="mode-summary">${summary}</div><div class="route-list">${rows.map(r=>`<div class="route"><div class="route-badge" style="background:${routeColor(r)}">${esc(r.route_name)}</div><div><strong>${esc(r.route_long_name||"Scheduled route")}</strong><small>${fmt(r.trips)} scheduled trips</small></div><div class="route-metric">${fmt(r.served_stops)}<small>served stops</small></div></div>`).join("")}</div>`
    }
    function changes(rows){
      const grouped={stop:{},route:{},trip:{}}; rows.forEach(x=>(grouped[x.entity_type]??={})[x.change_type]=+x.changed_entities);
      return `<div class="change-grid">${Object.entries(grouped).map(([name,v])=>`<div class="change"><span class="entity">${name} changes</span><strong>${fmt(Object.values(v).reduce((a,b)=>a+b,0))}</strong><div class="split"><span class="added">+ ${fmt(v.ADDED||0)} added</span><span class="modified">● ${fmt(v.MODIFIED||0)} modified</span><span class="removed">- ${fmt(v.REMOVED||0)} removed</span></div></div>`).join("")}</div>`;
    }
    function render(d){
      if(d.state!=="ready"){document.querySelector("#main").innerHTML=`<section class="waiting"><div class="waiting-orbit"></div><h2>Dashboard online, marts pending</h2><p>${esc(d.message)}</p></section>`;return}
      document.querySelector("#pulse").classList.add("ready");document.querySelector("#status").textContent="Published marts online";
      document.querySelector("#snapshot").textContent=d.summary.snapshot_date||"Available";
      document.querySelector("#generated").textContent=`Updated ${new Date(d.generated_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}`;
      const s=d.summary;
      document.querySelector("#main").innerHTML=`
        <section class="kpis">${metric("Scheduled trips",s.scheduled_trips,"Latest official service window")}${metric("Stop events",s.stop_events,"Quality-tested scheduled calls")}${metric("Active stops",s.active_stops,"Served in the latest snapshot")}${metric("Active routes",s.active_routes,"Official ATM network")}${metric("Service days",s.service_days,"Calendar and holiday enriched")}</section>
        <section class="dashboard-grid">
          <article class="panel"><div class="panel-head"><div><h2>Departures by service hour</h2><p>GTFS hours remain valid beyond midnight.</p></div><span class="tag">Supply rhythm</span></div>${hourly(d.departures)}</article>
          <article class="panel"><div class="panel-head"><div><h2>Route coverage</h2><p>Distinct scheduled stops by line.</p></div><span class="tag">Network</span></div>${routes(d.route_coverage)}</article>
          <article class="panel wide"><div class="panel-head"><div><h2>Scheduled service calendar</h2><p>Daily trip volume across weekdays, weekends, and holidays.</p></div><span class="tag">${fmt(d.service_days.length)} days</span></div>${trend(d.service_days)}</article>
          <article class="panel"><div class="panel-head"><div><h2>Stop constellation</h2><p>Every official stop, with the busiest locations labelled.</p></div><span class="tag">${fmt(d.stops.length)} points</span></div>${map(d.stops)}</article>
          <article class="panel"><div class="panel-head"><div><h2>Network change ledger</h2><p>Added, modified, and removed GTFS entities.</p></div><span class="tag">Version-aware</span></div>${changes(d.changes)}</article>
          <article class="panel wide"><div class="panel-head"><div><h2>From source file to visual evidence</h2><p>Every published metric crosses the same observable quality gates.</p></div><span class="tag">Lineage</span></div><div class="pipeline"><div class="stage"><b>01 / ARCHIVE</b><span>GTFS snapshot</span><small>SHA-256 and immutable object key</small></div><div class="stage"><b>02 / VALIDATE</b><span>Quality gate</span><small>Schema, keys, times, coordinates</small></div><div class="stage"><b>03 / MODEL</b><span>dbt warehouse</span><small>History, facts, dimensions</small></div><div class="stage"><b>04 / TEST</b><span>48 assertions</span><small>Relationships and business rules</small></div><div class="stage"><b>05 / OBSERVE</b><span>Visual mart</span><small>Read-only BI role</small></div></div></article>
        </section>`;
    }
    async function refresh(){try{const r=await fetch("/api/dashboard",{cache:"no-store"});render(await r.json())}catch(e){document.querySelector("#status").textContent="Reconnecting to dashboard API"}}
    refresh();setInterval(refresh,15000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
