/* AutoShorts Hub - tek dosyalik arayuz uygulamasi.
   Bagimlilik: Chart.js (ayni klasorde). Baska dis kaynak kullanilmaz; Raspberry
   Pi cevrimdisi olsa bile arayuz acilir. */

"use strict";

/* ------------------------------------------------------------------ */
/* Yardimcilar                                                         */
/* ------------------------------------------------------------------ */

const $ = (sel) => document.querySelector(sel);

/* innerHTML'e yazilan her deger buradan gecer (XSS korumasi). */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

const nf = new Intl.NumberFormat("tr-TR");

function fmtInt(n) { return nf.format(Number(n) || 0); }

function fmtMoney(n) {
  const v = Number(n) || 0;
  return "$" + (v >= 100 ? nf.format(Math.round(v)) : nf.format(Math.round(v * 100) / 100));
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return v + " B";
  if (v < 1048576) return (v / 1024).toFixed(1) + " KB";
  if (v < 1073741824) return (v / 1048576).toFixed(1) + " MB";
  return (v / 1073741824).toFixed(2) + " GB";
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function timeAgo(iso) {
  if (!iso) return "hiç";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(diff)) return "—";
  if (diff < 90) return "az önce";
  if (diff < 3600) return Math.round(diff / 60) + " dk önce";
  if (diff < 86400) return Math.round(diff / 3600) + " sa önce";
  return Math.round(diff / 86400) + " gün önce";
}

function minutesToTime(min) {
  const m = Math.max(0, Math.min(1439, Number(min) || 0));
  return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
}

function timeToMinutes(value) {
  const [h, m] = String(value || "0:0").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function toast(message, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ------------------------------------------------------------------ */
/* API sarmalayici                                                     */
/* ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    showLogin();
    throw new Error("Oturum gerekli.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "İstek başarısız (" + res.status + ")");
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* Durum ve yonlendirme                                                */
/* ------------------------------------------------------------------ */

const state = { loginRequired: false, charts: [] };

const VIEWS = {
  panel: { title: "Panel", render: viewPanel },
  kanallar: { title: "Kanallar", render: viewChannels },
  icerikler: { title: "İçerikler", render: viewContents },
  plan: { title: "Yayın Planı", render: viewPlan },
  ayarlar: { title: "Ayarlar", render: viewSettings },
};

function destroyCharts() {
  state.charts.forEach((c) => c.destroy());
  state.charts = [];
}

function route() {
  const hash = (location.hash || "#/panel").replace(/^#\//, "");
  const name = VIEWS[hash] ? hash : "panel";
  $("#page-title").textContent = VIEWS[name].title;
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === name);
  });
  destroyCharts();
  const view = $("#view");
  view.innerHTML = '<p class="muted">Yükleniyor…</p>';
  Promise.resolve(VIEWS[name].render())
    .catch((err) => {
      view.innerHTML = '<div class="note">' + esc(err.message) + "</div>";
    });
}

window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ */
/* Giris                                                               */
/* ------------------------------------------------------------------ */

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-password").focus();
}

function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
}

async function checkSession() {
  const st = await api("/api/auth/state");
  state.loginRequired = st.login_required;
  updateChips(st);
  if (!st.login_required) { showApp(); route(); return; }

  try {
    await api("/api/jobs?limit=1");
    showApp();
    route();
  } catch {
    showLogin();
  }
}

function updateChips(st) {
  const apiChip = $("#chip-api");
  apiChip.textContent = st.youtube_api_key ? "API anahtarı aktif" : "API anahtarı yok";
  apiChip.classList.toggle("ok", !!st.youtube_api_key);
  const oauthChip = $("#chip-oauth");
  oauthChip.textContent = st.youtube_oauth ? "OAuth hazır" : "OAuth yok";
  oauthChip.classList.toggle("ok", !!st.youtube_oauth);
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/auth/login", { method: "POST", body: { password: $("#login-password").value } });
    showApp();
    route();
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.reload();
});

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

async function viewPanel() {
  const [overview, videos] = await Promise.all([
    api("/api/overview"),
    api("/api/videos?days=7&limit=8"),
  ]);

  const t = overview.totals;
  const r = overview.revenue;
  const view = $("#view");

  const jobs = Object.entries(overview.jobs || {}).map(([name, j]) =>
    "<tr><td>" + esc(jobLabel(name)) + "</td><td><span class=\"badge " + esc(j.status) + "\">" +
    esc(j.status) + "</span></td><td class=\"muted\">" + timeAgo(j.started_at) +
    "</td></tr>").join("");

  view.innerHTML = `
    <div class="grid">
      <div class="card"><span class="label">Kanal</span>
        <div class="value">${fmtInt(t.channels)}</div>
        <div class="sub">${fmtInt(t.tracked_videos)} video takipte</div></div>
      <div class="card"><span class="label">Abone</span>
        <div class="value">${fmtInt(t.subscribers)}</div></div>
      <div class="card"><span class="label">Toplam izlenme</span>
        <div class="value">${fmtInt(t.views)}</div></div>
      <div class="card accent"><span class="label">Tahmini gelir · 30 gün</span>
        <div class="value">${fmtMoney(r.total_30d)}</div>
        <div class="sub">7 gün: ${fmtMoney(r.total_7d)} · ${fmtInt(r.views_7d)} izlenme</div></div>
      <div class="card"><span class="label">Yayın kuyruğu</span>
        <div class="value">${fmtInt((overview.publications || {}).pending || 0)}</div>
        <div class="sub">${fmtInt((overview.publications || {}).published || 0)} yayınlandı</div></div>
    </div>

    <div class="panel">
      <h2>Günlük izlenme ve tahmini gelir <small>son 30 gün</small></h2>
      <div class="chart-wrap"><canvas id="chart-revenue"></canvas></div>
    </div>

    <div class="panel">
      <h2>Kanallar</h2>
      <table><thead><tr>
        <th>Kanal</th><th class="num">Abone</th><th class="num">İzlenme</th>
        <th class="num">Video</th><th>Son toplama</th><th>Durum</th>
      </tr></thead><tbody>
        ${(overview.channels || []).map((c) => `
          <tr>
            <td><strong>${esc(c.title || c.yt_channel_id)}</strong>
              <div class="muted">${esc(c.handle ? "@" + c.handle : c.yt_channel_id)}</div></td>
            <td class="num">${fmtInt(c.subscribers)}</td>
            <td class="num">${fmtInt(c.total_views)}</td>
            <td class="num">${fmtInt(c.video_count)}</td>
            <td class="muted">${timeAgo(c.last_polled_at)}</td>
            <td>${c.is_active
              ? (c.last_error ? '<span class="neg" title="' + esc(c.last_error) + '">hata</span>' : '<span class="pos">aktif</span>')
              : '<span class="muted">pasif</span>'}</td>
          </tr>`).join("") || '<tr><td colspan="6" class="muted">Henüz kanal eklenmedi.</td></tr>'}
      </tbody></table>
    </div>

    <div class="panel">
      <h2>Son 7 günde öne çıkan videolar</h2>
      <table><thead><tr>
        <th>Video</th><th class="num">İzlenme</th><th class="num">7 günlük artış</th>
        <th class="num">Begeni</th><th>Yayın</th>
      </tr></thead><tbody>
        ${(videos.videos || []).map((v) => `
          <tr>
            <td>${esc(v.title)}${v.is_short ? ' <span class="badge pending">Shorts</span>' : ""}</td>
            <td class="num">${fmtInt(v.views)}</td>
            <td class="num pos">+${fmtInt(v.views_gained)}</td>
            <td class="num">${fmtInt(v.likes)}</td>
            <td class="muted">${fmtDateTime(v.published_at)}</td>
          </tr>`).join("") || '<tr><td colspan="5" class="muted">Veri yok.</td></tr>'}
      </tbody></table>
    </div>

    <div class="panel">
      <h2>Arka plan görevleri</h2>
      <table><thead><tr><th>Görev</th><th>Durum</th><th>Son çalışma</th></tr></thead>
      <tbody>${jobs || '<tr><td colspan="3" class="muted">Henüz çalışmadı.</td></tr>'}</tbody></table>
    </div>`;

  renderRevenueChart(r);
}

function jobLabel(name) {
  return {
    collect_all_channels: "İstatistik toplama",
    process_due_publications: "Yayın kuyruğu",
    prune_old_stats: "Veri budama",
  }[name] || name;
}

function renderRevenueChart(r) {
  const ctx = $("#chart-revenue");
  if (!ctx || typeof Chart === "undefined") return;
  state.charts.push(new Chart(ctx, {
    data: {
      labels: r.days.map((d) => d.slice(5).split("-").reverse().join("/")),
      datasets: [
        {
          type: "bar",
          label: "İzlenme",
          data: r.views_series,
          backgroundColor: "rgba(140,153,166,.28)",
          yAxisID: "y",
          order: 2,
        },
        {
          type: "line",
          label: "Tahmini gelir ($)",
          data: r.series,
          borderColor: "#e5a93d",
          backgroundColor: "rgba(229,169,61,.14)",
          fill: true,
          tension: .32,
          pointRadius: 2,
          yAxisID: "y1",
          order: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#8c99a6", boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: "#8c99a6", maxTicksLimit: 12 }, grid: { color: "rgba(42,51,61,.5)" } },
        y: { ticks: { color: "#8c99a6" }, grid: { color: "rgba(42,51,61,.5)" }, position: "left" },
        y1: { ticks: { color: "#e5a93d" }, grid: { display: false }, position: "right" },
      },
    },
  }));
}

/* ------------------------------------------------------------------ */
/* Kanallar                                                            */
/* ------------------------------------------------------------------ */

async function viewChannels() {
  const data = await api("/api/channels");
  const view = $("#view");

  view.innerHTML = `
    <div class="panel">
      <h2>Kanal ekle</h2>
      <form id="form-add-channel" class="form-row">
        <div class="field" style="flex:1">
          <label>Kanal ID (UC…), @handle veya kanal bağlantısı</label>
          <input type="text" id="add-ref" placeholder="ör. @kanalim veya UCx…" required>
        </div>
        <button class="btn primary" type="submit">Ekle ve topla</button>
      </form>
      <p class="hint">Kanal eklenirken <b>YouTube Data API anahtarı</b> gerekir.
        Ayarlar → Entegrasyon bölümüne bakın.</p>
    </div>
    <div id="channel-list"></div>`;

  $("#form-add-channel").addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = event.target.querySelector("button");
    btn.disabled = true;
    try {
      const res = await api("/api/channels", { method: "POST", body: { ref: $("#add-ref").value.trim() } });
      toast("Kanal eklendi: " + (res.channel.title || res.channel.yt_channel_id), "ok");
      viewChannels();
    } catch (err) {
      toast(err.message, "err");
      btn.disabled = false;
    }
  });

  const list = $("#channel-list");
  list.innerHTML = (data.channels || []).map((c) => channelCard(c)).join("")
    || '<div class="panel"><p class="muted">Henüz kanal yok. Yukarıdan ekleyin.</p></div>';

  list.querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", () => saveChannel(btn.dataset.save));
  });
  list.querySelectorAll("[data-collect]").forEach((btn) => {
    btn.addEventListener("click", () => collectChannel(btn.dataset.collect, btn));
  });
  list.querySelectorAll("[data-connect]").forEach((btn) => {
    btn.addEventListener("click", () => connectChannel(btn.dataset.connect));
  });
}

function channelCard(c) {
  return `
  <div class="panel">
    <div class="form-row" style="justify-content:space-between">
      <div style="display:flex;gap:12px;align-items:center;min-width:0">
        ${c.thumbnail_url ? '<img class="thumb" style="width:64px;height:64px;border-radius:50%" src="' + esc(c.thumbnail_url) + '" alt="">' : ""}
        <div style="min-width:0">
          <strong>${esc(c.title || c.yt_channel_id)}</strong>
          <div class="muted">${esc(c.handle ? "@" + c.handle : c.yt_channel_id)} ·
            ${fmtInt(c.subscribers)} abone · ${fmtInt(c.total_views)} izlenme</div>
          ${c.last_error ? '<div class="neg" style="font-size:12.5px">' + esc(c.last_error) + "</div>" : ""}
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn small" data-collect="${esc(c.yt_channel_id)}">Şimdi topla</button>
        <button class="btn small ${c.has_oauth ? "" : "primary"}" data-connect="${esc(c.yt_channel_id)}">
          ${c.has_oauth ? "Yeniden bağla" : "YouTube'a bağla"}</button>
      </div>
    </div>

    <form class="form-row" id="form-ch-${esc(c.yt_channel_id)}">
      <div class="field"><label>RPM ($ / 1000 izlenme)</label>
        <input type="number" step="0.01" min="0" name="rpm" value="${esc(c.rpm)}" style="width:110px"></div>
      <div class="field"><label>Günlük yükleme sınırı</label>
        <input type="number" min="1" max="25" name="daily_upload_limit" value="${esc(c.daily_upload_limit)}" style="width:90px"></div>
      <div class="field"><label>Yayın penceresi başlangıç</label>
        <input type="time" name="publish_window_start" value="${minutesToTime(c.publish_window_start)}"></div>
      <div class="field"><label>Yayın penceresi bitiş</label>
        <input type="time" name="publish_window_end" value="${minutesToTime(c.publish_window_end)}"></div>
      <div class="field"><label>Durum</label>
        <select name="is_active" style="width:110px">
          <option value="true" ${c.is_active ? "selected" : ""}>Aktif</option>
          <option value="false" ${!c.is_active ? "selected" : ""}>Pasif</option>
        </select></div>
      <button class="btn small" type="button" data-save="${esc(c.yt_channel_id)}">Kaydet</button>
    </form>
  </div>`;
}

async function saveChannel(channelId) {
  const form = $("#form-ch-" + CSS.escape(channelId));
  const body = {
    rpm: Number(form.rpm.value),
    daily_upload_limit: Number(form.daily_upload_limit.value),
    publish_window_start: timeToMinutes(form.publish_window_start.value),
    publish_window_end: timeToMinutes(form.publish_window_end.value),
    is_active: form.is_active.value === "true",
  };
  try {
    await api("/api/channels/" + encodeURIComponent(channelId), { method: "PATCH", body });
    toast("Kanal ayarları kaydedildi.", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function collectChannel(channelId, btn) {
  btn.disabled = true;
  btn.textContent = "Toplanıyor…";
  try {
    const res = await api("/api/channels/" + encodeURIComponent(channelId) + "/collect", { method: "POST" });
    const s = res.summary || {};
    toast("Toplandı: " + (s.tracked_videos || 0) + " video, " + (s.new_videos || 0) + " yeni", "ok");
    viewChannels();
  } catch (err) {
    toast(err.message, "err");
    btn.disabled = false;
    btn.textContent = "Şimdi topla";
  }
}

async function connectChannel(channelId) {
  try {
    const res = await api("/api/oauth/start?channel_id=" + encodeURIComponent(channelId));
    if (res.authorization_url) window.open(res.authorization_url, "_blank");
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ------------------------------------------------------------------ */
/* Icerikler                                                           */
/* ------------------------------------------------------------------ */

async function viewContents() {
  const data = await api("/api/contents");
  const view = $("#view");

  view.innerHTML = `
    <div class="panel">
      <h2>Video dosyası yükle</h2>
      <form id="form-upload">
        <div class="form-row">
          <div class="field" style="flex:1"><label>Dosya</label>
            <input type="file" id="up-file" accept="video/*" required></div>
          <div class="field" style="flex:1"><label>Başlık (boş bırakılırsa dosya adı)</label>
            <input type="text" id="up-title"></div>
        </div>
        <div class="form-row">
          <div class="field" style="flex:2"><label>Açıklama</label>
            <input type="text" id="up-description"></div>
          <div class="field"><label>Etiketler (virgülle)</label>
            <input type="text" id="up-tags" placeholder="shorts, oyun"></div>
        </div>
        <button class="btn primary" type="submit" id="up-btn">Yükle</button>
      </form>
      <p class="hint">Dosya karması (sha256) parmak izi olarak kullanılır: birebir aynı dosya
        ikinci kez eklenemez ve kanallar arası kural motoru bunu "aynı içerik" sayar.</p>
    </div>

    <div class="panel">
      <h2>Harici içerik kaydı</h2>
      <form id="form-reference">
        <div class="form-row">
          <div class="field" style="flex:1"><label>Başlık</label>
            <input type="text" id="ref-title" required></div>
          <div class="field" style="flex:2"><label>Açıklama</label>
            <input type="text" id="ref-description"></div>
        </div>
        <div class="form-row">
          <div class="field" style="flex:1"><label>Etiketler (virgülle)</label>
            <input type="text" id="ref-tags" placeholder="shorts, oyun"></div>
        </div>
        <button class="btn" type="submit">Kaydet</button>
      </form>
      <p class="hint">Dosya yüklemeden içerik planlamak için kullanılır; "aynı içerik" kontrolü
        başlık + açıklama üzerinden yapılır.</p>
    </div>

    <div class="panel">
      <h2>İçerik kütüphanesi</h2>
      <table><thead><tr>
        <th>Başlık</th><th>Tür</th><th>Boyut</th><th>Eklenme</th><th></th>
      </tr></thead><tbody id="content-rows"></tbody></table>
    </div>`;

  renderContentRows(data.contents || []);

  $("#form-upload").addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = $("#up-btn");
    const fileInput = $("#up-file");
    if (!fileInput.files.length) { toast("Dosya seçilmedi.", "err"); return; }

    const form = new FormData();
    form.append("file", fileInput.files[0]);
    form.append("title", $("#up-title").value);
    form.append("description", $("#up-description").value);
    form.append("tags", $("#up-tags").value);

    btn.disabled = true;
    btn.textContent = "Yükleniyor…";
    try {
      await api("/api/contents/upload", { method: "POST", body: form });
      toast("İçerik yüklendi.", "ok");
      viewContents();
    } catch (err) {
      toast(err.message, "err");
      btn.disabled = false;
      btn.textContent = "Yükle";
    }
  });

  $("#form-reference").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/contents", {
        method: "POST",
        body: {
          title: $("#ref-title").value,
          description: $("#ref-description").value,
          tags: ($("#ref-tags").value || "").split(",").map((s) => s.trim()).filter(Boolean),
        },
      });
      toast("İçerik kaydedildi.", "ok");
      viewContents();
    } catch (err) {
      toast(err.message, "err");
    }
  });
}

function renderContentRows(contents) {
  const rows = $("#content-rows");
  if (!rows) return;
  rows.innerHTML = contents.map((c) => `
    <tr>
      <td><strong>${esc(c.title)}</strong>
        <div class="muted" style="font-size:12px">${esc(c.fingerprint.slice(0, 18))}…</div></td>
      <td>${c.source_kind === "upload" ? "Dosya" : "Referans"}</td>
      <td class="muted">${c.source_kind === "upload" ? fmtBytes(c.file_size) : "—"}</td>
      <td class="muted">${fmtDateTime(c.created_at)}</td>
      <td><button class="btn small danger" data-del="${c.id}">Sil</button></td>
    </tr>`).join("") || '<tr><td colspan="5" class="muted">Kütüphane boş.</td></tr>';

  rows.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("İçerik ve planları silinsin mi?")) return;
      try {
        await api("/api/contents/" + btn.dataset.del, { method: "DELETE" });
        toast("İçerik silindi.", "ok");
        viewContents();
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

/* ------------------------------------------------------------------ */
/* Yayin plani                                                         */
/* ------------------------------------------------------------------ */

async function viewPlan() {
  const [contents, channels, pubs] = await Promise.all([
    api("/api/contents"),
    api("/api/channels"),
    api("/api/publications"),
  ]);
  const view = $("#view");

  view.innerHTML = `
    <div class="panel">
      <h2>Yayın planla</h2>
      <form id="form-plan">
        <div class="form-row">
          <div class="field" style="flex:2"><label>İçerik</label>
            <select id="plan-content" required>
              ${(contents.contents || []).map((c) =>
                '<option value="' + c.id + '">' + esc(c.title) + "</option>").join("")
                || '<option value="">— içerik yok —</option>'}
            </select></div>
          <div class="field"><label>Başlangıç (boş = hemen)</label>
            <input type="datetime-local" id="plan-at"></div>
          <div class="field"><label>Mod</label>
            <select id="plan-mode">
              <option value="auto">Otomatik dağıt (kanallar arası ≥ 7 gün)</option>
              <option value="manual">Tek tek (istenen saat)</option>
            </select></div>
        </div>
        <div class="field"><label>Kanallar</label>
          <div class="checks" id="plan-channels">
            ${(channels.channels || []).map((c) => `
              <label><input type="checkbox" value="${esc(c.yt_channel_id)}">
                ${esc(c.title || c.yt_channel_id)}${c.has_oauth ? "" : ' <span class="muted">(bağlı değil)</span>'}</label>`).join("")
              || '<span class="muted">Kanal yok.</span>'}
          </div>
        </div>
        <button class="btn primary" type="submit" style="margin-top:12px">Planı oluştur</button>
      </form>
    </div>

    <div id="plan-result"></div>

    <div class="panel">
      <h2>Kuyruk ve geçmiş</h2>
      <table><thead><tr>
        <th>Zaman</th><th>İçerik</th><th>Kanal</th><th>Durum</th><th>Not</th><th></th>
      </tr></thead><tbody id="pub-rows"></tbody></table>
    </div>`;

  renderPublicationRows(pubs.publications || []);

  $("#form-plan").addEventListener("submit", async (event) => {
    event.preventDefault();
    const contentId = Number($("#plan-content").value);
    if (!contentId) { toast("Önce içerik ekleyin.", "err"); return; }

    const channelIds = [...$("#plan-channels").querySelectorAll("input:checked")].map((i) => i.value);
    if (!channelIds.length) { toast("En az bir kanal seçin.", "err"); return; }

    const mode = $("#plan-mode").value;
    const body = { content_id: contentId, channel_ids: channelIds };
    const atValue = $("#plan-at").value;
    if (atValue) body[mode === "auto" ? "start_at" : "at"] = new Date(atValue).toISOString();

    try {
      const res = await api("/api/publications" + (mode === "auto" ? "/auto" : ""), {
        method: "POST", body,
      });
      showPlanResult(res);
      const pubs2 = await api("/api/publications");
      renderPublicationRows(pubs2.publications || []);
      toast((res.created || []).length + " yayın planlandı.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
}

function showPlanResult(res) {
  const created = (res.created || []).map((c) => `
    <div class="note">
      <strong>${esc(c.publication.channel_title)}</strong> ·
      ${fmtDateTime(c.publication.scheduled_at)}
      ${c.adjusted ? '<span class="badge pending">kural gereği ertelendi</span>'
                   : '<span class="badge published">istenen zamanda</span>'}
      ${(c.reasons || []).length ? '<div class="muted" style="margin-top:4px">' + esc(c.reasons.join(" ")) + "</div>" : ""}
    </div>`).join("");

  const skipped = (res.skipped || []).map((s) => `
    <div class="note" style="border-left-color:var(--negative)">
      <strong>${esc(s.channel_title)}</strong>: ${esc(s.reason)}
    </div>`).join("");

  $("#plan-result").innerHTML = (created || skipped)
    ? '<div class="panel"><h2>Planlama sonucu</h2>' + created + skipped + "</div>"
    : "";
}

function renderPublicationRows(pubs) {
  const rows = $("#pub-rows");
  if (!rows) return;
  rows.innerHTML = pubs.map((p) => `
    <tr>
      <td>${fmtDateTime(p.scheduled_at)}</td>
      <td>${esc(p.content_title)}</td>
      <td>${esc(p.channel_title)}</td>
      <td><span class="badge ${esc(p.status)}">${statusLabel(p.status)}</span></td>
      <td class="muted" style="max-width:260px">${esc(p.rule_note || p.last_error || "")}</td>
      <td>${p.status === "pending"
        ? '<button class="btn small danger" data-cancel="' + p.id + '">İptal</button>' : ""}</td>
    </tr>`).join("") || '<tr><td colspan="6" class="muted">Kuyruk boş.</td></tr>';

  rows.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/publications/" + btn.dataset.cancel, { method: "DELETE" });
        toast("Plan iptal edildi.", "ok");
        const pubs2 = await api("/api/publications");
        renderPublicationRows(pubs2.publications || []);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

function statusLabel(status) {
  return {
    pending: "sırada", published: "yayında", failed: "başarısız", cancelled: "iptal",
  }[status] || status;
}

/* ------------------------------------------------------------------ */
/* Ayarlar                                                             */
/* ------------------------------------------------------------------ */

async function viewSettings() {
  const [settings, st] = await Promise.all([api("/api/settings"), api("/api/auth/state")]);
  const s = settings.settings;
  const view = $("#view");

  view.innerHTML = `
    <div class="panel">
      <h2>Entegrasyon durumu</h2>
      <table><tbody>
        <tr><td>YouTube Data API anahtarı</td>
          <td>${st.youtube_api_key ? '<span class="pos">tanımlı</span>' : '<span class="neg">eksik</span>'}</td></tr>
        <tr><td>YouTube OAuth istemcisi (video yükleme)</td>
          <td>${st.youtube_oauth ? '<span class="pos">tanımlı</span>' : '<span class="neg">eksik</span>'}</td></tr>
      </tbody></table>
      <p class="hint">
        Bu değerler ortam değişkenlerinden okunur (docker-compose / CasaOS uygulama ayarları):
        <code>YOUTUBE_API_KEY</code>, <code>YOUTUBE_CLIENT_ID</code>,
        <code>YOUTUBE_CLIENT_SECRET</code>. Değişiklik sonrası uygulamayı yeniden başlatın.
      </p>
      <p class="hint">OAuth için Google Cloud Console'da "Web uygulaması" türünde istemci
        oluşturun; yönlendirme adresi olarak <code>http://&lt;pi-adresi&gt;:8080/api/oauth/callback</code>
        ekleyin. Kapsam olarak yalnızca <code>youtube.upload</code> istenir.</p>
    </div>

    <div class="panel">
      <h2>Yayın kuralları</h2>
      <form id="form-rules">
        <div class="form-row">
          <div class="field"><label>Kanallar arası minimum boşluk (gün)</label>
            <input type="number" min="1" max="60" id="rule-gap" value="${esc(s.cross_channel_gap_days)}" style="width:130px"></div>
          <div class="field"><label>Aynı kanalda tekrar (gün)</label>
            <input type="number" min="1" max="365" id="rule-repeat" value="${esc(s.same_channel_repeat_days)}" style="width:130px"></div>
          <div class="field"><label>Varsayılan günlük yükleme sınırı</label>
            <input type="number" min="1" max="25" id="rule-limit" value="${esc(s.default_daily_upload_limit)}" style="width:130px"></div>
          <div class="field"><label>Varsayılan RPM ($)</label>
            <input type="number" step="0.01" min="0" id="rule-rpm" value="${esc(s.default_rpm)}" style="width:110px"></div>
        </div>
        <button class="btn primary" type="submit">Kaydet</button>
      </form>
      <p class="hint">Kanallar arası boşluk kuralı, aynı içeriğin iki kanalda aynı gün
        yayınlanmasını engeller; otomatik dağıtım bu değeri kullanır.</p>
    </div>

    <div class="panel">
      <h2>Çalışma bilgisi</h2>
      <p class="hint">Uygulama 7/24 çalışır: istatistik toplama (varsayılan 30 dk),
        yayın kuyruğu işleme (5 dk) ve gece veri budama (04:10) görevleri
        zamanlayıcıda döner. Sıklık <code>POLL_INTERVAL_MINUTES</code> ve
        <code>PUBLISH_CHECK_INTERVAL_MINUTES</code> ile değiştirilebilir.</p>
    </div>`;

  $("#form-rules").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          cross_channel_gap_days: Number($("#rule-gap").value),
          same_channel_repeat_days: Number($("#rule-repeat").value),
          default_daily_upload_limit: Number($("#rule-limit").value),
          default_rpm: Number($("#rule-rpm").value),
        },
      });
      toast("Ayarlar kaydedildi.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
}

/* ------------------------------------------------------------------ */
/* Baslangic                                                           */
/* ------------------------------------------------------------------ */

checkSession().catch((err) => {
  toast("Sunucuya ulaşılamıyor: " + err.message, "err");
  showLogin();
});
