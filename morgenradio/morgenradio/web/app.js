/* Morgenradio — afspiller
 *
 * Skifter mellem værternes lydklip (spilles her på siden) og sange (spilles
 * på din egen Spotify via Spotify Web API). Sangene ligger aldrig i vores
 * filer — de streames fra Spotify, så det er lovligt.
 *
 * To måder at spille musikken på:
 *  - "Denne browser": Spotify Web Playback SDK (computer: Chrome/Edge/Firefox).
 *  - En Spotify-enhed (telefonens app, højttaler …) via Spotify Connect.
 */
(() => {
  "use strict";
  const CFG = window.RADIO_CONFIG || {};
  const CLIENT_ID = CFG.spotifyClientId || "";
  const REDIRECT = location.origin + location.pathname;
  const SCOPES = "streaming user-read-email user-read-private user-read-playback-state user-modify-playback-state";
  const $ = (id) => document.getElementById(id);
  const audio = $("audio");
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem("mr_" + k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("mr_" + k, JSON.stringify(v)); } catch {} },
    del(k) { try { localStorage.removeItem("mr_" + k); } catch {} },
  };

  const S = {
    show: null, base: "", i: -1, playing: false, deviceId: store.get("device") || "",
    sdkPlayer: null, sdkDeviceId: "", monitor: null, trackStarted: false, trackPos: 0,
    trackDeadline: 0, seq: 0, busy: false, wakeLock: null, token: store.get("token"),
  };

  const fmt = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const status = (t) => { $("status").textContent = t || ""; };
  const isMobile = /iPhone|iPad|Android/i.test(navigator.userAgent);
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ── Spotify login (PKCE, ingen server nødvendig) ─────────────────────────
  const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  async function login() {
    if (!CLIENT_ID) return status("Mangler Spotify Client ID — se opsætningsguiden (SPOTIFY_CLIENT_ID).");
    const verifier = b64url(crypto.getRandomValues(new Uint8Array(64)));
    const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    store.set("verifier", verifier);
    const p = new URLSearchParams({ client_id: CLIENT_ID, response_type: "code", redirect_uri: REDIRECT,
      code_challenge_method: "S256", code_challenge: challenge, scope: SCOPES });
    location.href = "https://accounts.spotify.com/authorize?" + p;
  }
  async function tokenRequest(body) {
    const r = await fetch("https://accounts.spotify.com/api/token", { method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ client_id: CLIENT_ID, ...body }) });
    if (!r.ok) throw new Error("Spotify-login fejlede (" + r.status + ")");
    const j = await r.json();
    S.token = { access: j.access_token, refresh: j.refresh_token || S.token?.refresh, exp: Date.now() + (j.expires_in - 60) * 1000 };
    store.set("token", S.token);
  }
  async function handleRedirect() {
    const q = new URLSearchParams(location.search);
    if (q.get("error")) status("Spotify-login blev afbrudt.");
    if (q.get("code")) {
      try { await tokenRequest({ grant_type: "authorization_code", code: q.get("code"), redirect_uri: REDIRECT, code_verifier: store.get("verifier") }); }
      catch (e) { status(e.message); }
      store.del("verifier");
    }
    if (q.get("code") || q.get("error")) history.replaceState(null, "", REDIRECT);
  }
  async function getToken() {
    if (!S.token) return null;
    if (Date.now() > S.token.exp) {
      try { await tokenRequest({ grant_type: "refresh_token", refresh_token: S.token.refresh }); }
      catch { S.token = null; store.del("token"); updateLoginUI(); return null; }
    }
    return S.token.access;
  }
  async function api(path, opt = {}) {
    for (let n = 0; n < 3; n++) {
      const tok = await getToken();
      if (!tok) throw new Error("Ikke logget ind på Spotify");
      const r = await fetch("https://api.spotify.com/v1" + path, { ...opt,
        headers: { Authorization: "Bearer " + tok, "Content-Type": "application/json", ...(opt.headers || {}) } });
      if (r.status === 401) { S.token.exp = 0; continue; }
      if (r.status === 429) { await new Promise((ok) => setTimeout(ok, 1000 * (+r.headers.get("Retry-After") || 2))); continue; }
      if (r.status === 204) return null;
      const txt = await r.text();
      if (!r.ok) { let m = txt; try { m = JSON.parse(txt).error.message; } catch {} throw new Error(m || "Spotify-fejl " + r.status); }
      return txt ? JSON.parse(txt) : null;
    }
    throw new Error("Spotify svarer ikke");
  }

  // ── Afspilning i browseren (Web Playback SDK) ────────────────────────────
  function loadSdk() {
    if (S.sdkPromise) return S.sdkPromise;
    S.sdkPromise = new Promise((resolve) => {
      window.onSpotifyWebPlaybackSDKReady = () => {
        const p = new Spotify.Player({ name: (S.show?.station || "Morgenradio") + " (browser)", volume: 0.85,
          getOAuthToken: (cb) => getToken().then(cb) });
        p.addListener("ready", ({ device_id }) => { S.sdkDeviceId = device_id; resolve(p); fillDevices(); });
        p.addListener("not_ready", () => { S.sdkDeviceId = ""; });
        p.addListener("initialization_error", ({ message }) => { status("Browser-afspilning virker ikke her: " + message); resolve(null); });
        p.addListener("authentication_error", () => { status("Log ind med Spotify igen."); resolve(null); });
        p.addListener("account_error", () => { status("Browser-afspilning kræver Spotify Premium."); resolve(null); });
        p.connect();
        S.sdkPlayer = p;
      };
      const s = document.createElement("script");
      s.src = "https://sdk.scdn.co/spotify-player.js";
      document.head.appendChild(s);
    });
    return S.sdkPromise;
  }

  // ── Enheder ──────────────────────────────────────────────────────────────
  async function fillDevices() {
    const sel = $("device");
    const opts = [];
    if (S.token) {
      if (!isMobile) opts.push({ id: "browser", name: "Denne browser" });
      try {
        const d = await api("/me/player/devices");
        for (const x of d.devices || []) if (x.id !== S.sdkDeviceId) opts.push({ id: x.id, name: `${x.name} (${x.type.toLowerCase()})` });
      } catch (e) { status(e.message); }
    }
    opts.push({ id: "none", name: "Uden musik — kun værterne" });
    sel.innerHTML = opts.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("");
    const want = opts.find((o) => o.id === S.deviceId) ? S.deviceId : (S.token ? (isMobile ? (opts[0].id) : "browser") : "none");
    sel.value = want; S.deviceId = want;
    $("deviceHint").textContent = !S.token
      ? "Log ind med Spotify (Premium) for at få rigtige sange mellem indslagene."
      : isMobile ? "Åbn Spotify-appen kort, så telefonen dukker op på listen, og tryk Opdatér. Hold skærmen tændt, mens radioen kører."
      : "Vil du hellere høre musikken på telefonen eller en højttaler? Åbn Spotify der og tryk Opdatér.";
  }
  function updateLoginUI() { $("login").hidden = !!S.token; fillDevices(); }

  async function targetDevice() {
    if (S.deviceId === "none") return null;
    if (S.deviceId === "browser") {
      const p = await loadSdk();
      if (!p || !S.sdkDeviceId) return null;
      return S.sdkDeviceId;
    }
    return S.deviceId;
  }

  // ── Afvikling ────────────────────────────────────────────────────────────
  const items = () => S.show?.items || [];

  async function stopSpotify() {
    clearInterval(S.monitor); S.monitor = null;
    if (S.spotifyActive) {
      S.spotifyActive = false;
      try { if (S.sdkPlayer && S.deviceId === "browser") await S.sdkPlayer.pause(); else await api("/me/player/pause", { method: "PUT" }); } catch {}
    }
  }

  async function playIndex(i) {
    if (i < 0) i = 0;
    const seq = ++S.seq;
    await stopSpotify();
    audio.pause();
    if (seq !== S.seq) return;
    if (i >= items().length) return finished();
    S.i = i; S.playing = true; render();
    const it = items()[i];
    if (it.type === "clip") {
      audio.src = S.base + it.src;
      audio.currentTime = 0;
      try { await audio.play(); } catch (e) { S.playing = false; render(); status("Tryk play for at fortsætte."); }
    } else {
      const dev = await targetDevice();
      if (!dev) return playIndex(i + 1);  // ingen musik → næste indslag
      try {
        status("");
        await api("/me/player/play?device_id=" + encodeURIComponent(dev), { method: "PUT", body: JSON.stringify({ uris: [it.uri], position_ms: 0 }) });
        try { await api("/me/player/repeat?state=off&device_id=" + encodeURIComponent(dev), { method: "PUT" }); } catch {}
        S.spotifyActive = true; S.trackStarted = false; S.trackPos = 0;
        S.trackDeadline = Date.now() + 15000;
        S.monitor = setInterval(() => watchTrack(it, seq), 1500);
      } catch (e) {
        status("Kunne ikke starte sangen på Spotify: " + e.message + " — springer videre.");
        setTimeout(() => S.i === i && playIndex(i + 1), 2500);
      }
    }
  }

  async function watchTrack(it, seq) {
    if (S.busy || seq !== S.seq) return;
    S.busy = true;
    try { await watchTrackInner(it, seq); } finally { S.busy = false; }
  }
  async function watchTrackInner(it, seq) {
    let st;
    try {
      if (S.deviceId === "browser" && S.sdkPlayer) {
        const s = await S.sdkPlayer.getCurrentState();
        st = s && { uri: s.track_window?.current_track?.uri, playing: !s.paused, pos: s.position, dur: s.duration };
      } else {
        const s = await api("/me/player");
        st = s && { uri: s.item?.uri, playing: s.is_playing, pos: s.progress_ms, dur: s.item?.duration_ms };
      }
    } catch { return; }
    if (!st || seq !== S.seq) return;
    const dur = st.dur || it.duration_ms;
    if (!S.trackStarted) {
      if (st.uri === it.uri && st.playing) S.trackStarted = true;
      else if (Date.now() > S.trackDeadline) { status("Sangen startede ikke — springer videre."); return playIndex(S.i + 1); }
      return;
    }
    const lastPos = S.trackPos;
    S.trackPos = st.pos;
    progress(st.pos / 1000, dur / 1000);
    const nearEnd = st.pos >= dur - 1800;                       // ~sidste 2 sek. → videre til værterne
    const movedOn = st.uri && st.uri !== it.uri;                // Spotify har selv skiftet sang
    const endedReset = !st.playing && st.pos < 1500 && lastPos > dur - 8000;  // sangen sluttede mellem to tjek
    if (nearEnd || movedOn || endedReset) return playIndex(S.i + 1);
    if (!st.playing && S.playing) { S.playing = false; render(); }       // pauset i Spotify
    if (st.playing && !S.playing) { S.playing = true; render(); }
  }

  function finished() {
    S.playing = false; S.i = items().length; render();
    $("kicker").textContent = "Slut for i dag";
    $("nowtitle").textContent = "Tak fordi du lyttede";
    $("nowsub").textContent = "Næste udsendelse er klar i morgen tidlig.";
    releaseWake();
  }

  async function togglePlay() {
    if (S.sdkPlayer) try { await S.sdkPlayer.activateElement(); } catch {}
    if (S.i < 0 || S.i >= items().length) { acquireWake(); return playIndex(0); }
    const it = items()[S.i];
    if (it.type === "clip") {
      if (audio.paused) { await audio.play().catch(() => {}); S.playing = true; acquireWake(); }
      else { audio.pause(); S.playing = false; }
    } else {
      try {
        if (S.playing) { S.deviceId === "browser" && S.sdkPlayer ? await S.sdkPlayer.pause() : await api("/me/player/pause", { method: "PUT" }); S.playing = false; }
        else { S.deviceId === "browser" && S.sdkPlayer ? await S.sdkPlayer.resume() : await api("/me/player/play", { method: "PUT" }); S.playing = true; }
      } catch (e) { status(e.message); }
    }
    render();
  }

  async function acquireWake() { try { S.wakeLock = await navigator.wakeLock?.request("screen"); } catch {} }
  function releaseWake() { try { S.wakeLock?.release(); } catch {} S.wakeLock = null; }

  // ── Visning ──────────────────────────────────────────────────────────────
  function progress(cur, dur) {
    $("bar").style.width = (dur ? Math.min(100, (cur / dur) * 100) : 0) + "%";
    $("tcur").textContent = fmt(cur); $("tdur").textContent = fmt(dur);
  }

  function render() {
    const it = items()[S.i];
    $("play").textContent = S.playing ? "❚❚" : "▶";
    $("play").setAttribute("aria-label", S.playing ? "Pause" : "Afspil");
    $("onair").classList.toggle("live", S.playing);
    [...$("rundown").children].forEach((li, n) => { li.classList.toggle("active", n === S.i); li.classList.toggle("done", n < S.i); });
    if (!it) return;
    const art = $("art");
    if (it.type === "clip") {
      $("kicker").textContent = "I studiet";
      $("nowtitle").textContent = it.label;
      const hosts = [...new Set(it.transcript.map((t) => t.vaert))];
      $("nowsub").textContent = hosts.join(" & ");
      art.innerHTML = `<span>${esc(hosts.map((h) => h[0]).join(""))}</span>`;
      $("transcript").innerHTML = it.transcript.map((t, n) => `<p data-n="${n}"><b>${esc(t.vaert)}:</b> ${esc(t.tekst)}</p>`).join("");
    } else {
      $("kicker").textContent = "Nu spiller";
      $("nowtitle").textContent = it.name;
      $("nowsub").textContent = it.artist + (it.kobling ? " · " + it.kobling : "");
      art.innerHTML = it.image ? `<img src="${esc(it.image)}" alt="">` : "<span>♪</span>";
      $("transcript").innerHTML = `<p>♪ ${esc(it.artist)} – ${esc(it.name)}${it.url ? ` · <a href="${esc(it.url)}" target="_blank" rel="noopener">Åbn i Spotify</a>` : ""}</p>`;
      progress(0, it.duration_ms / 1000);
    }
    mediaSession(it);
  }

  function mediaSession(it) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: it.type === "clip" ? it.label : it.name,
      artist: it.type === "clip" ? (S.show.station || "Morgenradio") : it.artist,
      album: S.show.title,
      artwork: it.image ? [{ src: it.image, sizes: "640x640" }] : [],
    });
    navigator.mediaSession.playbackState = S.playing ? "playing" : "paused";
  }

  function renderShow() {
    const s = S.show;
    document.title = s.station + " — " + s.dato_tekst;
    $("station").textContent = s.station; $("slogan").textContent = s.slogan;
    $("date").textContent = s.dato_tekst + (s.test ? " · TESTUDSENDELSE" : "");
    $("title").textContent = s.title;
    $("podcastLink").href = S.base + s.podcast;
    let t = 0;
    $("rundown").innerHTML = s.items.map((it, n) => {
      const dur = it.type === "clip" ? it.duration : it.duration_ms / 1000;
      const start = fmt(t); t += dur;
      return it.type === "clip"
        ? `<li data-n="${n}"><span class="ic">🎙</span><span class="t">${esc(it.label)}<span class="s">${start} · ${esc([...new Set(it.transcript.map((x) => x.vaert))].join(" & "))}</span></span><span class="d">${fmt(dur)}</span></li>`
        : `<li data-n="${n}"><span class="ic">♪</span><span class="t">${esc(it.name)}<span class="s">${start} · ${esc(it.artist)}</span></span><span class="d">${fmt(dur)}</span></li>`;
    }).join("");
    $("sources").innerHTML = (s.sources || []).map((x) => `<li><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.titel)}</a> <span>${esc(x.kilde)}</span></li>`).join("");
    S.i = -1; S.playing = false;
    $("kicker").textContent = "Klar"; $("nowtitle").textContent = "Tryk på play for at starte radioen";
    $("nowsub").textContent = `${s.items.filter((x) => x.type === "track").length} sange · ca. ${Math.round(t / 60)} minutter`;
    progress(0, 0); render();
  }

  async function loadEpisode(path) {
    await stopSpotify(); audio.pause();
    S.base = path.replace(/\/?$/, "/");
    const r = await fetch(S.base + "show.json", { cache: "no-store" });
    S.show = await r.json();
    renderShow();
  }

  async function init() {
    await handleRedirect();
    updateLoginUI();
    if (S.token && !isMobile) loadSdk();
    try {
      const idx = await (await fetch("episodes/index.json", { cache: "no-store" })).json();
      if (!idx.length) throw 0;
      $("episode").innerHTML = idx.map((e) => `<option value="${esc(e.path)}">${esc(e.dato_tekst)}</option>`).join("");
      await loadEpisode(idx[0].path);
    } catch { $("date").textContent = "Ingen udsendelser endnu — den første kommer i morgen tidlig."; }
  }

  // ── Hændelser ────────────────────────────────────────────────────────────
  audio.addEventListener("ended", () => playIndex(S.i + 1));
  audio.addEventListener("timeupdate", () => {
    const it = items()[S.i]; if (!it || it.type !== "clip") return;
    progress(audio.currentTime, it.duration);
    let cur = -1; it.transcript.forEach((t, n) => { if (audio.currentTime >= t.start - 0.1) cur = n; });
    document.querySelectorAll("#transcript p").forEach((p) => p.classList.toggle("cur", +p.dataset.n === cur));
  });
  audio.addEventListener("pause", () => { if (items()[S.i]?.type === "clip" && !audio.ended) { S.playing = false; render(); } });
  audio.addEventListener("play", () => { S.playing = true; render(); });
  $("play").onclick = togglePlay;
  $("next").onclick = () => { acquireWake(); playIndex(S.i + 1); };
  $("prev").onclick = () => playIndex(Math.max(0, S.i - 1));
  $("login").onclick = login;
  $("refreshDevices").onclick = fillDevices;
  $("device").onchange = (e) => { S.deviceId = e.target.value; store.set("device", S.deviceId); if (S.deviceId === "browser") loadSdk(); };
  $("episode").onchange = (e) => loadEpisode(e.target.value);
  $("rundown").onclick = (e) => { const li = e.target.closest("li"); if (li) { acquireWake(); playIndex(+li.dataset.n); } };
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", togglePlay);
    navigator.mediaSession.setActionHandler("pause", togglePlay);
    navigator.mediaSession.setActionHandler("nexttrack", () => playIndex(S.i + 1));
    navigator.mediaSession.setActionHandler("previoustrack", () => playIndex(Math.max(0, S.i - 1)));
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S.playing) acquireWake(); });
  init();
})();
