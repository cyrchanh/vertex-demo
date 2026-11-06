/* Two small SVG charts for the AI research section, with hover tooltips and table views. */
(() => {
  "use strict";
  const D = window.VERTEX_DATA;
  const NS = "http://www.w3.org/2000/svg";
  const tip = document.getElementById("tooltip");
  const v = (n) => getComputedStyle(document.documentElement).getPropertyValue("--" + n).trim();
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
  function showTip(html, e) {
    tip.innerHTML = html; tip.classList.add("show");
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY - r.height - 10;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y < 8) y = e.clientY + 16;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  const hideTip = () => tip.classList.remove("show");
  const row = (dot, label, val) => `<div class="tt-row"><span>${dot ? `<i class="dot" style="background:${dot}"></i>` : ""}${label}</span><b>${val}</b></div>`;
  // bar with 4px rounded top, square at the baseline
  const barPath = (x, y, w, h) => { const r = Math.min(4, w / 2, h); return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`; };

  // ---------- chart 1: hits per duel ----------
  function duels() {
    const host = document.getElementById("chartDuels"); host.innerHTML = "";
    const W = 520, H = 270, m = { l: 34, r: 8, t: 18, b: 52 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Grouped bars: hits landed by the creator and by the bot in each of 9 duels" }, host);
    const data = D.duels, max = 140;
    const iw = W - m.l - m.r, ih = H - m.t - m.b, gw = iw / data.length;
    const y = (val) => m.t + ih - (val / max) * ih;
    for (let t = 0; t <= max; t += 40) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? v("axis") : v("grid"), "stroke-width": 1 }, svg);
      el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, svg).textContent = t;
    }
    const bw = Math.min(16, gw * 0.3);
    data.forEach((d, i) => {
      const cx = m.l + gw * i + gw / 2;
      el("path", { d: barPath(cx - bw - 1, y(d.you), bw, y(0) - y(d.you)), fill: v("you") }, svg);
      el("path", { d: barPath(cx + 1, y(d.bot), bw, y(0) - y(d.bot)), fill: v("opp") }, svg);
      el("text", { x: cx, y: H - m.b + 16, "text-anchor": "middle" }, svg).textContent = d.duel;
      if (i === data.length - 1) {
        el("text", { x: cx - 1 - bw / 2, y: y(d.you) - 5, "text-anchor": "middle", class: "lbl" }, svg).textContent = d.you;
        el("text", { x: cx + 1 + bw / 2, y: y(d.bot) - 5, "text-anchor": "middle", class: "lbl" }, svg).textContent = d.bot;
      }
      const hit = el("rect", { x: m.l + gw * i, y: m.t, width: gw, height: ih, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", (e) => showTip(`<div class="tt-title">Duel ${d.duel} · ${d.arch === "LSTM" ? "LSTM (memory) policy" : "MLP policy"}</div>` +
        row(v("you"), "Creator's hits", d.you) + row(v("opp"), "Bot's hits", d.bot) +
        row(null, "Bot hits per creator hit", (d.bot / d.you).toFixed(2)) + row(null, "Creator's score (0–1)", d.score.toFixed(2)), e));
      hit.addEventListener("pointerleave", hideTip);
    });
    // architecture brackets under the axis labels
    const groups = [["MLP policies (v1–v2)", 0, 4], ["LSTM + opponent model (v3 on)", 5, 8]];
    for (const [name, a, b] of groups) {
      const x1 = m.l + gw * a + 6, x2 = m.l + gw * (b + 1) - 6, yy = H - m.b + 28;
      el("path", { d: `M${x1},${yy - 4}V${yy}H${x2}V${yy - 4}`, fill: "none", stroke: v("axis"), "stroke-width": 1 }, svg);
      el("text", { x: (x1 + x2) / 2, y: yy + 15, "text-anchor": "middle" }, svg).textContent = name;
    }
    el("text", { x: m.l, y: 10 }, svg).textContent = "Hits landed per duel";
    const lg = document.createElement("div"); lg.className = "legend";
    lg.innerHTML = `<span><i class="dot" style="background:${v("you")}"></i>Creator (human)</span><span><i class="dot" style="background:${v("opp")}"></i>RL bot</span><span>Duel number →</span>`;
    host.appendChild(lg);
    document.getElementById("tableDuels").innerHTML = `<table><thead><tr><th>Duel</th><th>Policy</th><th class="num">Creator hits</th><th class="num">Bot hits</th><th class="num">Score</th></tr></thead><tbody>${
      data.map((d) => `<tr><td>${d.duel}</td><td>${d.arch}</td><td class="num">${d.you}</td><td class="num">${d.bot}</td><td class="num">${d.score.toFixed(2)}</td></tr>`).join("")}</tbody></table>`;
  }

  // ---------- chart 2: transfer audit scatter ----------
  function audit() {
    const host = document.getElementById("chartAudit"); host.innerHTML = "";
    const W = 520, H = 270, m = { l: 44, r: 12, t: 18, b: 52 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Scatter: judgement on bot fights versus judgement on human fights for 11 checkpoints" }, host);
    const xmin = -0.4, xmax = 0.25, ymin = -1.7, ymax = 0.1;
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x = (val) => m.l + ((val - xmin) / (xmax - xmin)) * iw;
    const y = (val) => m.t + ih - ((val - ymin) / (ymax - ymin)) * ih;
    for (const t of [-1.5, -1, -0.5, 0]) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? v("axis") : v("grid") }, svg);
      el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, svg).textContent = t.toFixed(1);
    }
    for (const t of [-0.4, -0.2, 0, 0.2]) {
      el("line", { x1: x(t), x2: x(t), y1: m.t, y2: m.t + ih, stroke: t === 0 ? v("axis") : v("grid") }, svg);
      el("text", { x: x(t), y: m.t + ih + 16, "text-anchor": "middle" }, svg).textContent = (t > 0 ? "+" : "") + t.toFixed(1);
    }
    el("text", { x: m.l + iw / 2, y: H - 14, "text-anchor": "middle" }, svg).textContent = "Judgement on bot fights → better";
    el("text", { x: m.l, y: 10 }, svg).textContent = "Judgement on the human's fights (above 0 = beats health bar)";
    el("text", { x: x(0.23), y: y(0) - 6, class: "lbl", "text-anchor": "end" }, svg).textContent = "No checkpoint gets above 0";
    // group identical points so overlaps share one tooltip
    const groups = new Map();
    for (const p of D.audit) { const k = p.bot + "|" + p.human; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); }
    for (const pts of groups.values()) {
      const p = pts[0], cx = x(p.bot), cy = y(p.human);
      el("circle", { cx, cy, r: 5.5, fill: v("you"), stroke: v("page"), "stroke-width": 2 }, svg);
      if (pts.some((q) => q.checkpoint === "champion-v3")) {
        el("text", { x: cx + 9, y: cy + 4, class: "lbl" }, svg).textContent = "v3 champion";
      }
      const hit = el("circle", { cx, cy, r: 14, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", (e) => showTip(`<div class="tt-title">${pts.map((q) => q.checkpoint).join("<br>")}</div>` +
        row(null, "Bot-fight judgement", (p.bot > 0 ? "+" : "") + p.bot.toFixed(2)) + row(null, "Human-fight judgement", p.human.toFixed(2)), e));
      hit.addEventListener("pointerleave", hideTip);
    }
    const lg = document.createElement("div"); lg.className = "legend";
    lg.innerHTML = `<span>Each dot is one trained checkpoint (11). Spearman ρ = −0.73, p = 0.01, on a duel none of them saw.</span>`;
    host.appendChild(lg);
    document.getElementById("tableAudit").innerHTML = `<table><thead><tr><th>Checkpoint</th><th class="num">Bot fights</th><th class="num">Human fights</th></tr></thead><tbody>${
      D.audit.map((p) => `<tr><td>${p.checkpoint}</td><td class="num">${p.bot.toFixed(2)}</td><td class="num">${p.human.toFixed(2)}</td></tr>`).join("")}</tbody></table>`;
  }

  const render = () => { duels(); audit(); };
  render();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
})();
