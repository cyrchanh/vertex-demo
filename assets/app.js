/* Vertex demo: a browser replay of real fights exported from the mod's replay files.
 * Frame layout (per fighter): [x, y, z, yaw, health, charge, flags, keys]
 *   flags: 1 onGround, 2 sprinting, 4 sneaking, 8 swing started, 16 hurt this tick
 *   keys:  bit 0 W, 1 S, 2 A, 3 D, 4 jump, 5 sneak, 6 sprint; -1 = unknown (not sent by servers)
 * Fighter 0 is always "You". */
(() => {
  "use strict";
  const DATA = window.VERTEX_DATA;
  const $ = (id) => document.getElementById(id);
  const css = {};
  const readCss = () => {
    const s = getComputedStyle(document.documentElement);
    for (const k of ["you", "opp", "ink", "ink-2", "muted", "grid", "axis", "surface", "page",
      "arena-floor", "arena-line", "good", "bad", "neutral", "surface-2"]) css[k] = s.getPropertyValue("--" + k).trim();
  };
  readCss();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readCss(); draw(); drawTimeline(); });
  const color = (i) => (i === 0 ? css.you : css.opp);

  const state = { fight: null, t: 0, playing: false, speed: 1, persp: 0, cam: null, ground: [0, 0], lastEx: -2 };

  // ---------- frame access ----------
  const dur = (f) => (f.frames.length - 1) * f.frameTicks;
  const lerpAngle = (a, b, k) => { let d = ((b - a + 540) % 360) - 180; return a + d * k; };
  function sample(f, t) {
    const pos = Math.max(0, Math.min(f.frames.length - 1, t / f.frameTicks));
    const i0 = Math.floor(pos), i1 = Math.min(i0 + 1, f.frames.length - 1), k = pos - i0;
    const a = f.frames[i0], b = f.frames[i1];
    return a.map((p, n) => {
      const q = b[n];
      return { x: p[0] + (q[0] - p[0]) * k, y: p[1] + (q[1] - p[1]) * k, z: p[2] + (q[2] - p[2]) * k,
        yaw: lerpAngle(p[3], q[3], k), hp: p[4] + (q[4] - p[4]) * k, charge: p[5], flags: p[6], keys: p[7] };
    });
  }
  // recent swings: frames within the last few ticks that started a swing
  function swingAge(f, t, n) {
    const i = Math.floor(t / f.frameTicks);
    for (let j = i; j >= 0 && (i - j) * f.frameTicks <= 6; j--) if (f.frames[j][n][6] & 8) return t - j * f.frameTicks;
    return -1;
  }
  // ground height under each fighter: last y while on the ground
  function groundAt(f, t, n) {
    const i = Math.floor(t / f.frameTicks);
    for (let j = i; j >= 0 && i - j < 80; j--) if (f.frames[j][n][6] & 1) return f.frames[j][n][1];
    return f.frames[Math.max(0, i)][n][1];
  }

  // ---------- picker ----------
  const picker = $("picker");
  DATA.fights.forEach((f, i) => {
    const b = document.createElement("button");
    b.setAttribute("role", "tab");
    b.innerHTML = `<p class="kind">${f.kind === "duel" ? "Self-play arena" : "Mod recording"}</p><b>${f.title}</b><span>${f.blurb}</span>`;
    b.addEventListener("click", () => load(i, true));
    picker.appendChild(b);
  });

  function load(i, play) {
    const f = DATA.fights[i];
    state.fight = f; state.t = 0; state.cam = null; state.lastEx = -2;
    [...picker.children].forEach((b, n) => b.setAttribute("aria-selected", n === i ? "true" : "false"));
    $("legendChips").innerHTML = f.names.map((n, k) => `<span class="chip"><i class="dot" style="background:${color(k)}"></i>${n}</span>`).join("");
    buildFighters();
    if (f.exchanges.length) state.t = Math.max(0, f.exchanges[0].start - 20);
    setPlaying(!!play);
    renderAll(true);
  }

  // ---------- fighter cards ----------
  function buildFighters() {
    const f = state.fight;
    $("fighters").innerHTML = f.names.map((n, k) => `
      <div class="fighter" data-k="${k}">
        <div class="fighter-head"><b><i class="dot" style="background:${color(k)}"></i>${n}</b><span class="hits"></span></div>
        <div class="bar-row"><span>Health</span><div class="bar"><i class="hp" style="background:${color(k)}"></i></div><span class="val hpv"></span></div>
        <div class="bar-row"><span>Charge</span><div class="bar"><i class="ch" style="background:${color(k)}"></i></div><span class="val chv"></span></div>
        <div class="keys">${f.keysKnown[k]
          ? `<span class="key w">W</span><span class="key a">A</span><span class="key s">S</span><span class="key d">D</span>
             <span class="mods"><span class="key jump">Jump</span><span class="key sneak">Sneak</span><span class="key sprint">Sprint</span></span>`
          : `<span class="unknown">Keys not visible: servers never send another player's inputs, and Vertex never guesses them.</span>`}
        </div>
      </div>`).join("");
  }
  function updateFighters(s) {
    const f = state.fight;
    const cards = $("fighters").children;
    for (let k = 0; k < 2; k++) {
      const c = cards[k], p = s[k];
      const landed = f.hits.filter((h) => h.a === k && h.t <= state.t).length;
      const total = f.hits.filter((h) => h.a === k).length;
      c.querySelector(".hits").textContent = `${landed} / ${total} hits`;
      c.querySelector(".hp").style.width = `${Math.max(0, Math.min(1, p.hp / 20)) * 100}%`;
      c.querySelector(".hpv").textContent = (p.hp / 2).toFixed(1) + " ♥";
      const ch = c.querySelector(".ch"), chv = c.querySelector(".chv");
      if (p.charge < 0) { ch.style.width = "0%"; chv.textContent = "unknown"; }
      else { ch.style.width = `${p.charge * 100}%`; ch.style.opacity = p.charge >= 0.9 ? 1 : 0.45; chv.textContent = Math.round(p.charge * 100) + "%"; }
      if (f.keysKnown[k] && p.keys >= 0) {
        const on = (sel, bit) => c.querySelector(sel).classList.toggle("on", !!(p.keys & (1 << bit)));
        on(".w", 0); on(".s", 1); on(".a", 2); on(".d", 3); on(".jump", 4); on(".sneak", 5);
        c.querySelector(".sprint").classList.toggle("on", !!(p.keys & 64) || !!(p.flags & 2));
      }
    }
  }

  // ---------- arena canvas ----------
  const arena = $("arena"), ctx = arena.getContext("2d");
  const tl = $("timeline"), tctx = tl.getContext("2d");
  function fit(cv, c) {
    const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(r.width * d) || cv.height !== Math.round(r.height * d)) {
      cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d);
    }
    c.setTransform(d, 0, 0, d, 0, 0);
    return [r.width, r.height];
  }
  new ResizeObserver(() => { draw(); drawTimeline(); }).observe(arena);

  function draw() {
    const f = state.fight; if (!f) return;
    const [W, H] = fit(arena, ctx);
    const s = sample(f, state.t);
    // camera: midpoint, zoom to keep both in frame
    const mx = (s[0].x + s[1].x) / 2, mz = (s[0].z + s[1].z) / 2;
    const gap = Math.hypot(s[0].x - s[1].x, s[0].z - s[1].z);
    const span = Math.max(9, Math.min(34, gap * 1.6 + 5));
    const target = { x: mx, z: mz, span };
    if (!state.cam) state.cam = { ...target };
    else for (const k of ["x", "z", "span"]) state.cam[k] += (target[k] - state.cam[k]) * 0.12;
    const cam = state.cam;
    const ppb = Math.min(W, H * 1.4) / cam.span; // pixels per block
    const sx = (x) => W / 2 + (x - cam.x) * ppb, sz = (z) => H / 2 + (z - cam.z) * ppb;

    ctx.fillStyle = css["arena-floor"]; ctx.fillRect(0, 0, W, H);
    // block grid
    ctx.lineWidth = 1;
    const x0 = Math.floor(cam.x - W / 2 / ppb), x1 = Math.ceil(cam.x + W / 2 / ppb);
    const z0 = Math.floor(cam.z - H / 2 / ppb), z1 = Math.ceil(cam.z + H / 2 / ppb);
    for (let x = x0; x <= x1; x++) { ctx.strokeStyle = x % 4 === 0 ? css.grid : css["arena-line"]; ctx.beginPath(); ctx.moveTo(Math.round(sx(x)) + .5, 0); ctx.lineTo(Math.round(sx(x)) + .5, H); ctx.stroke(); }
    for (let z = z0; z <= z1; z++) { ctx.strokeStyle = z % 4 === 0 ? css.grid : css["arena-line"]; ctx.beginPath(); ctx.moveTo(0, Math.round(sz(z)) + .5); ctx.lineTo(W, Math.round(sz(z)) + .5); ctx.stroke(); }

    // trails (last 2 s)
    for (let n = 0; n < 2; n++) {
      ctx.strokeStyle = color(n); ctx.lineWidth = 2; ctx.lineCap = "round";
      const steps = 40;
      for (let i = steps; i > 0; i--) {
        const ta = state.t - i * 1, tb = state.t - (i - 1) * 1;
        if (ta < 0) continue;
        const a = sample(f, ta)[n], b = sample(f, tb)[n];
        ctx.globalAlpha = 0.5 * (1 - i / steps);
        ctx.beginPath(); ctx.moveTo(sx(a.x), sz(a.z)); ctx.lineTo(sx(b.x), sz(b.z)); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // spacing line
    ctx.setLineDash([4, 5]); ctx.strokeStyle = css.axis; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(sx(s[0].x), sz(s[0].z)); ctx.lineTo(sx(s[1].x), sz(s[1].z)); ctx.stroke(); ctx.setLineDash([]);
    ctx.font = "600 12px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const lx = (sx(s[0].x) + sx(s[1].x)) / 2, lz = (sz(s[0].z) + sz(s[1].z)) / 2;
    const label = gap.toFixed(1) + " blocks";
    const tw = ctx.measureText(label).width + 12;
    ctx.fillStyle = css.surface; roundRect(ctx, lx - tw / 2, lz - 10, tw, 20, 6); ctx.fill();
    ctx.fillStyle = gap <= 3 ? css.ink : css.muted; ctx.fillText(label, lx, lz);

    // fighters
    for (let n = 0; n < 2; n++) {
      const p = s[n], X = sx(p.x), Z = sz(p.z);
      const h = Math.max(0, p.y - groundAt(f, state.t, n));
      const yawR = (p.yaw * Math.PI) / 180, dx = -Math.sin(yawR), dz = Math.cos(yawR);
      const ang = Math.atan2(dz, dx);
      // 3-block reach arc
      ctx.fillStyle = color(n); ctx.globalAlpha = 0.07;
      ctx.beginPath(); ctx.moveTo(X, Z); ctx.arc(X, Z, 3 * ppb, ang - 0.55, ang + 0.55); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
      // swing flash
      const sa = swingAge(f, state.t, n);
      if (sa >= 0) {
        ctx.strokeStyle = color(n); ctx.lineWidth = 3; ctx.globalAlpha = 1 - sa / 6;
        ctx.beginPath(); ctx.arc(X, Z, 2.2 * ppb, ang - 0.5, ang + 0.5); ctx.stroke(); ctx.globalAlpha = 1;
      }
      // shadow + body (lifted when airborne)
      const r = Math.max(8, 0.42 * ppb);
      ctx.fillStyle = css.ink; ctx.globalAlpha = 0.12; ctx.beginPath(); ctx.ellipse(X, Z, r, r * 0.8, 0, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1;
      const lift = Math.min(1.6, h) * ppb * 0.45, rr = r * (1 + Math.min(1.6, h) * 0.18);
      const BX = X, BZ = Z - lift;
      ctx.fillStyle = color(n); ctx.beginPath(); ctx.arc(BX, BZ, rr, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = css["arena-floor"]; ctx.lineWidth = 2; ctx.stroke();
      // facing nose
      ctx.strokeStyle = css.surface; ctx.lineWidth = 3; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(BX, BZ); ctx.lineTo(BX + dx * rr * 0.95, BZ + dz * rr * 0.95); ctx.stroke();
      // sword
      ctx.strokeStyle = color(n); ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.moveTo(BX + dx * rr, BZ + dz * rr); ctx.lineTo(BX + dx * (rr + 0.9 * ppb), BZ + dz * (rr + 0.9 * ppb)); ctx.stroke();
      // status tags
      const tags = [];
      if (h > 0.15) tags.push(`↑ ${h.toFixed(1)}`);
      if (p.flags & 2) tags.push("sprint");
      if (p.flags & 4) tags.push("sneak");
      if (tags.length) {
        ctx.font = "600 11px system-ui, sans-serif"; const txt = tags.join(" · ");
        const w = ctx.measureText(txt).width + 10;
        ctx.fillStyle = css.surface; ctx.globalAlpha = 0.92; roundRect(ctx, BX - w / 2, BZ + rr + 6, w, 17, 5); ctx.fill(); ctx.globalAlpha = 1;
        ctx.fillStyle = css["ink-2"]; ctx.fillText(txt, BX, BZ + rr + 14.5);
      }
    }

    // hit bursts
    for (const hit of f.hits) {
      const age = state.t - hit.t;
      if (age < 0 || age > 12) continue;
      const v = s[hit.v], X = sx(v.x), Z = sz(v.z) - Math.min(1.6, Math.max(0, v.y - groundAt(f, state.t, hit.v))) * ppb * 0.45;
      const k = age / 12;
      ctx.strokeStyle = color(hit.a); ctx.lineWidth = hit.kind === "CRIT" ? 4 : 2.5; ctx.globalAlpha = 1 - k;
      ctx.beginPath(); ctx.arc(X, Z, Math.max(8, 0.42 * ppb) + k * 1.4 * ppb, 0, Math.PI * 2); ctx.stroke();
      const word = { CRIT: "CRIT!", SPRINT_HIT: "SPRINT HIT", WEAK: "weak", STRONG: "hit", MOB: "hit", UNKNOWN: "hit" }[hit.kind] || "hit";
      ctx.font = `800 ${hit.kind === "CRIT" ? 16 : 13}px system-ui, sans-serif`;
      ctx.fillStyle = color(hit.a);
      ctx.fillText(word, X, Z - Math.max(8, 0.42 * ppb) - 14 - k * 16);
      ctx.globalAlpha = 1;
    }

    $("hudTime").textContent = `${(state.t / f.tps).toFixed(1)} s / ${(dur(f) / f.tps).toFixed(1)} s`;
    updateFighters(s);
  }
  function roundRect(c, x, y, w, h, r) { c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }

  // ---------- timeline ----------
  const verdictColor = (ex) => {
    const c = ex.coaching && ex.coaching[state.persp];
    if (!c) return css.neutral;
    return c.verdict === "GOOD" ? css.good : c.verdict === "MISTAKE" ? css.bad : css.neutral;
  };
  function drawTimeline() {
    const f = state.fight; if (!f) return;
    const [W, H] = fit(tl, tctx);
    const D = dur(f), pad = 10, xw = (t) => pad + (t / D) * (W - 2 * pad);
    tctx.clearRect(0, 0, W, H);
    const cur = currentExchange();
    f.exchanges.forEach((ex, i) => {
      tctx.fillStyle = verdictColor(ex); tctx.globalAlpha = i === cur.i ? 0.32 : 0.16;
      roundRect(tctx, xw(ex.start), 4, Math.max(4, xw(ex.end) - xw(ex.start)), H - 8, 4); tctx.fill();
    });
    tctx.globalAlpha = 1;
    tctx.strokeStyle = css.axis; tctx.lineWidth = 1; tctx.beginPath(); tctx.moveTo(pad, H / 2 + .5); tctx.lineTo(W - pad, H / 2 + .5); tctx.stroke();
    for (const h of f.hits) {
      const x = Math.round(xw(h.t)) + .5, len = h.kind === "CRIT" ? 18 : 11;
      tctx.strokeStyle = color(h.a); tctx.lineWidth = 2;
      tctx.beginPath();
      if (h.a === 0) { tctx.moveTo(x, H / 2 - 2); tctx.lineTo(x, H / 2 - 2 - len); } else { tctx.moveTo(x, H / 2 + 2); tctx.lineTo(x, H / 2 + 2 + len); }
      tctx.stroke();
    }
    const px = xw(state.t);
    tctx.strokeStyle = css.ink; tctx.lineWidth = 2; tctx.beginPath(); tctx.moveTo(px, 2); tctx.lineTo(px, H - 2); tctx.stroke();
    tctx.fillStyle = css.ink; tctx.beginPath(); tctx.arc(px, 4, 4, 0, Math.PI * 2); tctx.fill();
  }
  let dragging = false;
  const seekFromEvent = (e) => {
    const r = tl.getBoundingClientRect(), pad = 10;
    const k = Math.max(0, Math.min(1, (e.clientX - r.left - pad) / (r.width - 2 * pad)));
    state.t = k * dur(state.fight); state.cam = null; renderAll();
  };
  tl.addEventListener("pointerdown", (e) => { dragging = true; tl.setPointerCapture(e.pointerId); seekFromEvent(e); });
  tl.addEventListener("pointermove", (e) => { if (dragging) seekFromEvent(e); });
  tl.addEventListener("pointerup", () => { dragging = false; });

  // ---------- coaching panel ----------
  function currentExchange() {
    const exs = state.fight.exchanges;
    let i = exs.findIndex((e) => state.t >= e.start && state.t <= e.end);
    let live = true;
    if (i < 0) { live = false; for (let j = exs.length - 1; j >= 0; j--) if (exs[j].end < state.t) { i = j; break; } }
    return { i, live };
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const VERDICT = { GOOD: ["✓", "Good", "good"], MISTAKE: ["✕", "Mistake", "bad"], NEUTRAL: ["•", "Neutral", "neutral"] };

  function renderCoach(force) {
    const f = state.fight, { i, live } = currentExchange();
    const key = `${f.id}|${i}|${live}|${state.persp}`;
    if (!force && key === state.lastEx) return;
    state.lastEx = key;
    const el = $("coach");
    const exs = f.exchanges;
    const persp = f.kind === "mod"
      ? `<div class="persp" role="group" aria-label="Coach which fighter"><button data-p="0" class="${state.persp === 0 ? "on" : ""}">You</button><button data-p="1" class="${state.persp === 1 ? "on" : ""}">Opponent</button></div>`
      : "";
    if (i < 0) {
      el.innerHTML = `<div class="coach-top"><span class="coach-label">Coach</span>${persp}</div>
        <div class="idle"><h3>${f.kind === "mod" ? "Press play, or jump to an exchange" : "You vs. your own trained bot"}</h3>
        <p>${f.kind === "mod"
          ? "Vertex groups the fight into exchanges (bursts of hits less than a second apart). Each one gets a verdict and four-part coaching: what happened, why, what to do instead, and why that works."
          : "This duel was recorded server-side in the self-play arena, so both fighters' keys are exact. The bot is the LSTM policy with an opponent-prediction head. It sees you 200 ms late, like a real client would."}</p></div>
        <p class="source-line">${f.source}</p>`;
      bindPersp(); return;
    }
    const ex = exs[i];
    const t0 = (ex.anchor / f.tps).toFixed(1);
    const nav = `<span class="ex-nav">Exchange ${i + 1} of ${exs.length} · ${t0} s${live ? "" : " · just ended"}</span>`;
    const c = ex.coaching && ex.coaching[state.persp];
    if (!c) {
      const crits = f.hits.filter((h) => h.t >= ex.start && h.t <= ex.end && h.kind === "CRIT");
      const sprints = f.hits.filter((h) => h.t >= ex.start && h.t <= ex.end && h.kind === "SPRINT_HIT");
      const lead = ex.hitsBy[0] - ex.hitsBy[1];
      el.innerHTML = `<div class="coach-top">${nav}${persp}</div>
        <span class="verdict NEUTRAL"><i class="sw" style="background:${css.neutral}"></i>Exchange score</span>
        <h3>${lead > 0 ? "You won this exchange" : lead < 0 ? "The bot won this exchange" : "Even exchange"} (${ex.hitsBy[0]}–${ex.hitsBy[1]})</h3>
        <dl><dt>What happened</dt><dd>You landed ${ex.hitsBy[0]} hit${ex.hitsBy[0] === 1 ? "" : "s"}, the bot ${ex.hitsBy[1]}.
          ${crits.length ? ` Crits: ${crits.filter((h) => h.a === 0).length} by you, ${crits.filter((h) => h.a === 1).length} by the bot.` : ""}
          ${sprints.length ? ` Sprint hits: ${sprints.filter((h) => h.a === 0).length} by you, ${sprints.filter((h) => h.a === 1).length} by the bot.` : ""}</dd>
        <dt>Why this view is different</dt><dd>Arena duels are where the research happens: both fighters' keys are visible, so you can watch the bot's actual decisions (W, S, jump, sprint) next to yours.</dd>
        <dt>What the research found</dt><dd>The creator came out ahead in all 45 fights against every bot version. Bots that got better against other bots didn't get better at judging human fights (see the AI lab section below).</dd></dl>
        <p class="source-line">${f.source}</p>`;
      bindPersp(); return;
    }
    const [icon, word, cls] = VERDICT[c.verdict] || VERDICT.NEUTRAL;
    el.innerHTML = `<div class="coach-top">${nav}${persp}</div>
      <span class="verdict ${c.verdict}"><i class="sw" style="background:${css[cls] || css.neutral}"></i>${icon} ${word}</span>
      <h3>${esc(c.headline)}</h3>
      <dl>
        <dt>What happened</dt><dd>${esc(c.what)}</dd>
        <dt>Why</dt><dd>${esc(c.why)}</dd>
        <dt>Do instead</dt><dd class="instead">${esc(c.instead)}</dd>
        <dt>Why that works</dt><dd>${esc(c.insteadWhy)}</dd>
      </dl>
      <p class="src">📖 ${esc(c.source)}${c.inferred ? " · relies partly on estimated data" : ""}</p>
      ${c.notes && c.notes.length ? `<ul class="notes">${c.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      <p class="source-line">Written by the mod's rule-based explainer (<code>RuleBasedExplainer</code>) at the end of the fight.</p>`;
    bindPersp();
  }
  function bindPersp() {
    $("coach").querySelectorAll(".persp button").forEach((b) => b.addEventListener("click", () => {
      state.persp = +b.dataset.p; renderCoach(true); drawTimeline();
    }));
  }

  // ---------- playback ----------
  function setPlaying(p) {
    state.playing = p;
    if (p && state.t >= dur(state.fight)) { state.t = 0; state.cam = null; }
    $("btnPlay").textContent = p ? "❚❚" : "▶";
    $("btnPlay").setAttribute("aria-label", p ? "Pause" : "Play");
  }
  function renderAll(force) { draw(); drawTimeline(); renderCoach(force); }
  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (state.playing && state.fight) {
      state.t += dt * state.fight.tps * state.speed;
      if (state.t >= dur(state.fight)) { state.t = dur(state.fight); setPlaying(false); }
      renderAll();
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  function jumpExchange(dir) {
    const exs = state.fight.exchanges; if (!exs.length) return;
    const { i } = currentExchange();
    let j;
    if (dir > 0) j = exs.findIndex((e) => e.start > state.t + 1);
    else { j = -1; for (let k = exs.length - 1; k >= 0; k--) if (exs[k].start < state.t - 15) { j = k; break; } }
    if (j < 0) j = dir > 0 ? exs.length - 1 : 0;
    if (j === i && dir > 0 && j === exs.length - 1) return;
    state.t = exs[j].start; state.cam = null; renderAll();
  }
  $("btnPlay").addEventListener("click", () => setPlaying(!state.playing));
  $("btnNext").addEventListener("click", () => jumpExchange(1));
  $("btnPrev").addEventListener("click", () => jumpExchange(-1));
  document.querySelectorAll(".speed button").forEach((b) => b.addEventListener("click", () => {
    state.speed = +b.dataset.speed;
    document.querySelectorAll(".speed button").forEach((x) => x.classList.toggle("on", x === b));
  }));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select")) return;
    const r = $("replay").getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight) return; // only while the player is on screen
    const step = e.shiftKey ? 1 : 20;
    if (e.code === "Space") { e.preventDefault(); setPlaying(!state.playing); }
    else if (e.code === "ArrowRight") { e.preventDefault(); state.t = Math.min(dur(state.fight), state.t + step); renderAll(); }
    else if (e.code === "ArrowLeft") { e.preventDefault(); state.t = Math.max(0, state.t - step); state.cam = null; renderAll(); }
    else if (e.code === "KeyN") jumpExchange(1);
    else if (e.code === "KeyP") jumpExchange(-1);
  });

  // deep link: ?fight=<0-3>&t=<seconds>
  const q = new URLSearchParams(location.search);
  const qf = Math.max(0, Math.min(DATA.fights.length - 1, parseInt(q.get("fight") || "0", 10) || 0));
  load(qf, false);
  if (q.has("t")) { state.t = Math.min(dur(state.fight), parseFloat(q.get("t")) * state.fight.tps || 0); state.cam = null; renderAll(true); }
  // start playing once the player scrolls into view (unless the viewer prefers reduced motion)
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches && "IntersectionObserver" in window) {
    let started = false;
    new IntersectionObserver((ents, obs) => {
      if (!started && ents.some((x) => x.isIntersecting)) { started = true; setPlaying(true); obs.disconnect(); }
    }, { threshold: 0.5 }).observe($("arena"));
  }
})();
