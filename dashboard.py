"""Static overview page with price-history charts.

  python dashboard.py --out site      -> site/index.html (self-contained, no external requests)

Built by the GitHub workflow after every polling run and published via GitHub Pages.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import AppConfig, load_config
from notify import pretty_shop
from storage import Storage

GROUPS = [("9070 XT", "Radeon RX 9070 XT"), ("5070 Ti", "GeForce RTX 5070 Ti"), ("5080", "GeForce RTX 5080")]


def group_of(name: str) -> str:
    return next((label for key, label in GROUPS if key.lower() in name.lower()), "Weitere")


def build_data(cfg: AppConfig, st: Storage, now: datetime) -> dict:
    cards = []
    for p in cfg.products:
        pts = st.db.execute(
            "SELECT fetched_at, total_eur, shop, offer_url FROM price_points WHERE product_id=? ORDER BY fetched_at",
            (p.id,)).fetchall()
        points = [[int(datetime.fromisoformat(r["fetched_at"]).timestamp() * 1000), round(r["total_eur"], 2),
                   pretty_shop(r["shop"])] for r in pts]
        stats = st.stats(p.id, now, cfg.rules_default.median_days)
        avail = st.get_state(f"avail:{p.id}")
        last = pts[-1] if pts else None
        cards.append({
            "id": p.id, "label": p.label, "name": p.name, "group": p.group or group_of(p.name),
            "target": p.target_price or None,
            "available": avail == "1",
            "current": round(last["total_eur"], 2) if last and avail == "1" else None,
            "shop": pretty_shop(last["shop"]) if last and avail == "1" else None,
            "url": (last["offer_url"] if last and avail == "1" and not last["offer_url"].startswith("https://geizhals.de/redir/")
                    else p.urls[0]),
            "min": stats.min_ever, "median": stats.median_7d,
            "points": points,
        })
    return {"generated": int(now.timestamp() * 1000), "cards": cards,
            "groups": list(dict.fromkeys(c["group"] for c in cards))}


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.db:
        cfg.database = args.db
    st = Storage(cfg.db_path)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    html = render(build_data(cfg, st, datetime.now(ZoneInfo(cfg.poll.timezone))))
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"wrote {out / 'index.html'} ({len(html) // 1024} KB)")


TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GPU-Preisalarm</title>
<meta name="description" content="Bestpreise und Preisverlauf der überwachten Grafikkarten, Black Week 2026">
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series: #2a78d6; --good: #006300; --good-mark: #0ca30c; --hover: rgba(11,11,11,0.04);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series: #3987e5; --good: #0ca30c; --good-mark: #0ca30c; --hover: rgba(255,255,255,0.05);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series: #3987e5; --good: #0ca30c; --good-mark: #0ca30c; --hover: rgba(255,255,255,0.05);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 28px 16px 48px; }
header h1 { font-size: 22px; margin: 0 0 4px; font-weight: 650; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--ink-2); font-size: 14px; }
.controls { display: flex; gap: 6px; margin: 22px 0 14px; flex-wrap: wrap; }
.controls button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink-2); }
.controls button[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--muted); font-weight: 500; font-size: 12px; }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td a { color: inherit; text-decoration-color: var(--axis); text-underline-offset: 3px; }
.status { display: inline-flex; align-items: center; gap: 6px; }
.status .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
.status.ok .dot { background: var(--series); }
.status.hit { color: var(--good); font-weight: 600; }
.status.hit .dot { background: var(--good-mark); }
.status.off { color: var(--muted); }
.below { color: var(--good); font-weight: 600; }
h2 { font-size: 15px; font-weight: 600; margin: 34px 0 12px; color: var(--ink-2); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.chart-card { padding: 14px 14px 8px; position: relative; }
.chart-card .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.chart-card h3 { font-size: 15px; margin: 0; font-weight: 600; }
.chart-card .now { font-size: 20px; font-weight: 650; }
.chart-card .sub { color: var(--ink-2); font-size: 13px; margin: 2px 0 6px; min-height: 19px; }
.chart-card .sub.hit { color: var(--good); font-weight: 600; }
.plot { position: relative; height: 180px; }
.plot svg { display: block; width: 100%; height: 100%; overflow: visible; }
.plot svg:focus { outline: 2px solid var(--series); outline-offset: 2px; border-radius: 4px; }
.empty { height: 180px; display: grid; place-items: center; color: var(--muted); font-size: 13px; text-align: center;
  border: 1px dashed var(--grid); border-radius: 8px; margin-bottom: 6px; }
.tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 9px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,0.12);
  white-space: nowrap; display: none; z-index: 2; }
.tip strong { display: block; font-size: 14px; }
.tip span { color: var(--ink-2); }
.tip .key { display: inline-block; width: 12px; height: 2px; background: var(--series); vertical-align: middle; margin-right: 6px; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  .hide-sm { display: none; }
  th, td { padding: 8px 8px; }
  td:first-child, td:last-child { white-space: normal; }
  .status { align-items: flex-start; }
  .status .dot { margin-top: 6px; flex: none; }
  main { padding-top: 20px; }
}
footer { margin-top: 36px; color: var(--muted); font-size: 12px; }
footer a { color: inherit; }
</style>
</head>
<body>
<main>
  <header>
    <h1>GPU-Preisalarm</h1>
    <p id="meta"></p>
  </header>

  <div class="controls" role="group" aria-label="Zeitraum der Diagramme">
    <button type="button" data-range="86400000">24 Stunden</button>
    <button type="button" data-range="604800000">7 Tage</button>
    <button type="button" data-range="0" aria-pressed="true">Alles</button>
  </div>

  <section class="card table-wrap" aria-label="Übersicht">
    <table>
      <thead><tr><th>Karte</th><th class="num">Aktuell</th><th class="hide-sm">Shop</th><th class="num hide-sm">Ziel</th>
        <th class="num">Abstand</th><th class="num hide-sm">Tiefstpreis</th><th>Status</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </section>

  <div id="charts"></div>

  <footer>
    Bestpreis lieferbarer Angebote bei Whitelist-Händlern, nur Kartenpreis (ohne Versand).
    Quellen: Geizhals.de, Alternate. Aktualisiert bei jedem Abruf.
  </footer>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(() => {
  const DATA = JSON.parse(document.getElementById("data").textContent);
  const TZ = "Europe/Berlin";
  const fmtEur = v => v == null ? "–" : new Intl.NumberFormat("de-DE", {
    style: "currency", currency: "EUR", maximumFractionDigits: Number.isInteger(v) ? 0 : 2,
    minimumFractionDigits: Number.isInteger(v) ? 0 : 2 }).format(v);
  const fmtDiff = v => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmtEur(Math.abs(v)).replace(/\s?€/, " €");
  const fDateTime = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const fTime = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
  const fDay = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit" });
  const el = (tag, attrs = {}, text) => { const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

  document.getElementById("meta").textContent =
    "Black Week 2026 · " + DATA.cards.length + " Karten · Stand " + fDateTime.format(DATA.generated);

  // ---------- overview table ----------
  const rows = document.getElementById("rows");
  for (const c of DATA.cards) {
    const tr = el("tr");
    const name = el("td"); const a = el("a", { href: c.url, target: "_blank", rel: "noopener" }, c.label); name.append(a);
    const hit = c.current != null && c.target && c.current <= c.target;
    const diff = c.current != null && c.target ? c.current - c.target : null;
    const st = el("td"); const s = el("span", { class: "status " + (hit ? "hit" : c.available ? "ok" : "off") });
    s.append(el("span", { class: "dot", "aria-hidden": "true" }),
             document.createTextNode(hit ? "✓ Ziel erreicht" : c.available ? "Lieferbar" : "Nicht lieferbar"));
    st.append(s);
    const d = el("td", { class: "num" + (diff != null && diff <= 0 ? " below" : "") }, diff == null ? "–" : fmtDiff(diff));
    tr.append(name, el("td", { class: "num" }, fmtEur(c.current)), el("td", { class: "hide-sm" }, c.shop || "–"),
              el("td", { class: "num hide-sm" }, fmtEur(c.target)), d, el("td", { class: "num hide-sm" }, fmtEur(c.min)), st);
    rows.append(tr);
  }

  // ---------- charts ----------
  const charts = document.getElementById("charts");
  const plots = [];
  for (const g of DATA.groups) {
    const cards = DATA.cards.filter(c => c.group === g);
    if (!cards.length) continue;
    charts.append(el("h2", {}, g));
    const grid = el("div", { class: "grid" }); charts.append(grid);
    for (const c of cards) {
      const card = el("article", { class: "card chart-card" });
      const head = el("div", { class: "head" });
      head.append(el("h3", {}, c.label), el("span", { class: "now" }, c.current != null ? fmtEur(c.current) : ""));
      const hit = c.current != null && c.target && c.current <= c.target;
      const sub = el("div", { class: "sub" + (hit ? " hit" : "") },
        c.current != null ? (hit ? "✓ Ziel erreicht · " : "") + c.shop + (c.target ? " · Ziel " + fmtEur(c.target) : "")
                          : (c.target ? "Nicht lieferbar · Ziel " + fmtEur(c.target) : "Nicht lieferbar"));
      const plot = el("div", { class: "plot" });
      card.append(head, sub, plot); grid.append(card);
      plots.push({ c, plot });
    }
  }

  let range = 0;
  const niceStep = span => { const raw = span / 4, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; };

  function draw({ c, plot }) {
    plot.replaceChildren();
    const end = DATA.generated;
    const pts = c.points.filter(p => !range || p[0] >= end - range);
    if (!pts.length) {
      plot.className = "empty";
      plot.textContent = c.points.length ? "Keine Preise in diesem Zeitraum" : "Noch keine Preisdaten – derzeit kein passendes Angebot";
      return;
    }
    plot.className = "plot";
    const W = plot.clientWidth || 320, H = 180, L = 52, R = 10, T = 10, B = 24;
    let t0 = pts[0][0], t1 = Math.max(end, pts[pts.length - 1][0]);
    if (t1 - t0 < 3600e3) t0 = t1 - 3600e3;
    const vals = pts.map(p => p[1]).concat(c.target ? [c.target] : []);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 20) { lo -= 10; hi += 10; }
    const step = niceStep(hi - lo); lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const x = t => L + (t - t0) / (t1 - t0) * (W - L - R);
    const y = v => T + (hi - v) / (hi - lo) * (H - T - B);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", tabindex: "0",
      "aria-label": `Preisverlauf ${c.label}: aktuell ${fmtEur(pts[pts.length - 1][1])}` });
    // grid + y ticks
    for (let v = lo; v <= hi + 1e-6; v += step) {
      svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v === lo ? "var(--axis)" : "var(--grid)", "stroke-width": 1 }));
      const t = svgEl("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" });
      t.textContent = fmtEur(v); svg.append(t);
    }
    // x ticks
    const span = t1 - t0, n = Math.max(2, Math.min(5, Math.floor((W - L - R) / 80)));
    for (let i = 0; i <= n; i++) {
      const tt = t0 + span * i / n;
      const t = svgEl("text", { x: x(tt), y: H - 6, "text-anchor": i === 0 ? "start" : i === n ? "end" : "middle", class: "tick" });
      t.textContent = span <= 36 * 3600e3 ? fTime.format(tt) : fDay.format(tt); svg.append(t);
    }
    // target line (reference, not a series); its label is drawn above the price line
    let targetLabel = null;
    if (c.target) {
      svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(c.target), y2: y(c.target), stroke: "var(--muted)",
        "stroke-width": 1.5, "stroke-dasharray": "5 4" }));
      targetLabel = svgEl("text", { x: W - R, y: y(c.target) - 5, "text-anchor": "end", class: "tick",
        stroke: "var(--surface)", "stroke-width": 4, "paint-order": "stroke", "stroke-linejoin": "round" });
      targetLabel.textContent = "Ziel " + fmtEur(c.target);
    }
    // step line: a price holds until the next observation, the last one until now
    let d = `M${x(pts[0][0])},${y(pts[0][1])}`;
    for (let i = 1; i < pts.length; i++) d += `H${x(pts[i][0])}V${y(pts[i][1])}`;
    d += `H${x(t1)}`;
    svg.append(svgEl("path", { d, fill: "none", stroke: "var(--series)", "stroke-width": 2, "stroke-linejoin": "round" }));
    const lp = pts[pts.length - 1];
    svg.append(svgEl("circle", { cx: x(lp[0]), cy: y(lp[1]), r: 4, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2 }));
    if (targetLabel) svg.append(targetLabel);
    // hover layer
    const cross = svgEl("line", { y1: T, y2: H - B, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
    const dot = svgEl("circle", { r: 5, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
    svg.append(cross, dot);
    const tip = el("div", { class: "tip", role: "status" });
    plot.append(svg, tip);
    let idx = pts.length - 1;
    const show = i => {
      idx = Math.max(0, Math.min(pts.length - 1, i)); const p = pts[idx];
      cross.setAttribute("x1", x(p[0])); cross.setAttribute("x2", x(p[0])); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(p[0])); dot.setAttribute("cy", y(p[1])); dot.setAttribute("visibility", "visible");
      tip.replaceChildren(el("strong", {}, fmtEur(p[1])));
      const meta = el("span"); meta.append(el("i", { class: "key" }), document.createTextNode(p[2] + " · " + fDateTime.format(p[0])));
      tip.append(meta); tip.style.display = "block";
      const tx = x(p[0]), tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(W - tw, tx - tw / 2)) + "px";
      tip.style.top = Math.max(0, y(p[1]) - tip.offsetHeight - 12) + "px";
    };
    const hide = () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); };
    svg.addEventListener("pointermove", e => {
      const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
      let best = 0, bd = Infinity;
      pts.forEach((p, i) => { const dd = Math.abs(x(p[0]) - px); if (dd < bd) { bd = dd; best = i; } });
      show(best);
    });
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(idx));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", e => {
      if (e.key === "ArrowLeft") { show(idx - 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(idx + 1); e.preventDefault(); }
    });
  }

  const drawAll = () => plots.forEach(draw);
  document.querySelectorAll(".controls button").forEach(b => b.addEventListener("click", () => {
    range = Number(b.dataset.range);
    document.querySelectorAll(".controls button").forEach(o => o.setAttribute("aria-pressed", String(o === b)));
    drawAll();
  }));
  let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(drawAll, 80); }).observe(charts);
  drawAll();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
