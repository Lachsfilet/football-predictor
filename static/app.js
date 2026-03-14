/**
 * Football Predictor — Frontend Application
 */

const API = "";  // same origin

// ─────────────────────────────────────────
// Tab navigation
// ─────────────────────────────────────────

document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(`tab-${tab}`).classList.add("active");

        if (tab === "status") {
            loadStatus();
            loadLogs();
        }
    });
});

// ─────────────────────────────────────────
// Prediction
// ─────────────────────────────────────────

const predictBtn = document.getElementById("predict-btn");
const clearBtn   = document.getElementById("clear-btn");
const textarea   = document.getElementById("matches-input");
const results    = document.getElementById("results");

predictBtn.addEventListener("click", async () => {
    const text = textarea.value.trim();
    if (!text) {
        showResults([{ status: "error", message: "Please enter at least one match." }]);
        return;
    }

    predictBtn.disabled = true;
    predictBtn.textContent = "⏳ Predicting…";
    results.innerHTML = "<p class='loading'>Running predictions…</p>";
    results.classList.remove("hidden");

    try {
        const res = await fetch(`${API}/api/predictions/text`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();

        if (!res.ok) {
            showResults([{ status: "error", message: data.detail || "Server error" }]);
        } else {
            showResults(data.predictions || [data]);
        }
    } catch (err) {
        showResults([{ status: "error", message: `Network error: ${err.message}` }]);
    } finally {
        predictBtn.disabled = false;
        predictBtn.innerHTML = '<span class="btn-icon">🔮</span> Predict';
    }
});

clearBtn.addEventListener("click", () => {
    textarea.value = "";
    results.innerHTML = "";
    results.classList.add("hidden");
});

// ─────────────────────────────────────────
// Render prediction cards
// ─────────────────────────────────────────

function showResults(predictions) {
    results.innerHTML = "";
    results.classList.remove("hidden");

    predictions.forEach((pred) => {
        results.appendChild(buildPredictionCard(pred));
    });
}

function buildPredictionCard(pred) {
    const card = document.createElement("div");
    card.className = "prediction-card" + (pred.status === "error" ? " error" : "");

    if (pred.status === "error") {
        card.innerHTML = `
            <div class="pred-body">
                <p class="error-msg">⚠️ ${escHtml(pred.message)}</p>
            </div>`;
        return card;
    }

    const outcome = pred.predicted_outcome || "?";
    const probs   = pred.probabilities || {};
    const homeP   = probs.home_win ?? 0;
    const drawP   = probs.draw ?? 0;
    const awayP   = probs.away_win ?? 0;
    const conf    = pred.confidence ?? 0;

    const outcomeLabel = {
        HOME: `${escHtml(pred.home_team)} Win`,
        DRAW: "Draw",
        AWAY: `${escHtml(pred.away_team)} Win`,
    }[outcome] || outcome;

    const metaParts = [];
    if (pred.competition) metaParts.push(escHtml(pred.competition));
    if (pred.match_date)  metaParts.push(formatDate(pred.match_date));
    const metaStr = metaParts.join(" · ");

    const factorsHtml = (pred.key_factors || [])
        .map((f) => `<li>${escHtml(f)}</li>`)
        .join("");

    card.innerHTML = `
        <div class="pred-header">
            <div class="match-title">
                ${escHtml(pred.home_team)} vs ${escHtml(pred.away_team)}
            </div>
            ${metaStr ? `<div class="match-meta">${metaStr}</div>` : ""}
        </div>
        <div class="pred-body">
            <div class="outcome-badge outcome-${outcome}">
                ${outcomeLabel}
                <small style="font-weight:400; opacity:.8">&nbsp;· ${conf}% confidence</small>
            </div>

            <div class="prob-section">
                ${probRow("Home Win", homeP, "home")}
                ${probRow("Draw", drawP, "draw")}
                ${probRow("Away Win", awayP, "away")}
            </div>

            ${factorsHtml ? `
            <div class="factors-title">Key factors</div>
            <ul class="factor-list">${factorsHtml}</ul>` : ""}
        </div>`;

    return card;
}

function probRow(label, pct, cls) {
    return `
        <div class="prob-row">
            <div class="prob-label">${label}</div>
            <div class="prob-bar-wrap">
                <div class="prob-bar ${cls}" style="width:${pct}%"></div>
            </div>
            <div class="prob-pct">${pct}%</div>
        </div>`;
}

// ─────────────────────────────────────────
// System status
// ─────────────────────────────────────────

async function loadStatus() {
    const el = document.getElementById("status-content");
    el.innerHTML = "<p class='loading'>Loading…</p>";
    try {
        const res  = await fetch(`${API}/api/data/status`);
        const data = await res.json();
        const db   = data.database || {};
        const model = data.model || {};
        const sync  = data.last_sync || {};

        el.innerHTML = `
            ${statBox(db.teams ?? "–", "Teams")}
            ${statBox(db.matches_total ?? "–", "Total Matches")}
            ${statBox(db.matches_finished ?? "–", "Finished")}
            ${statBox(db.features_computed ?? "–", "Features")}
            ${statBox(model.ready ? "✓ Ready" : "✗ Not trained", "Model")}
            ${statBox(sync.at ? formatDate(sync.at) : "Never", "Last Sync")}
        `;
    } catch (e) {
        el.innerHTML = `<p class="error-msg">Failed to load: ${e.message}</p>`;
    }
}

function statBox(value, label) {
    return `
        <div class="stat-box">
            <div class="stat-value">${value}</div>
            <div class="stat-label">${label}</div>
        </div>`;
}

// ─────────────────────────────────────────
// Sync logs
// ─────────────────────────────────────────

async function loadLogs() {
    const el = document.getElementById("logs-content");
    el.innerHTML = "<p class='loading'>Loading…</p>";
    try {
        const res  = await fetch(`${API}/api/data/sync-logs?limit=15`);
        const data = await res.json();
        if (!data.length) {
            el.innerHTML = "<p class='empty'>No sync logs yet.</p>";
            return;
        }
        el.innerHTML = `
            <table class="logs-table">
                <thead>
                    <tr>
                        <th>Source</th><th>Operation</th>
                        <th>Status</th><th>Inserted</th><th>Time</th>
                    </tr>
                </thead>
                <tbody>
                    ${data.map((l) => `
                        <tr>
                            <td>${escHtml(l.source)}</td>
                            <td>${escHtml(l.operation)}</td>
                            <td><span class="badge badge-${l.status}">${l.status}</span></td>
                            <td>${l.records_inserted ?? 0}</td>
                            <td>${l.at ? formatDate(l.at) : ""}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>`;
    } catch (e) {
        el.innerHTML = `<p class="error-msg">Failed to load: ${e.message}</p>`;
    }
}

// ─────────────────────────────────────────
// Data management buttons
// ─────────────────────────────────────────

async function triggerAction(url, label) {
    const msgEl = document.getElementById("action-msg");
    msgEl.className = "action-msg info";
    msgEl.textContent = `⏳ ${label} started…`;
    msgEl.classList.remove("hidden");

    try {
        const res  = await fetch(`${API}${url}`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            msgEl.className = "action-msg success";
            msgEl.textContent = `✓ ${data.message || label + " started"}`;
        } else {
            msgEl.className = "action-msg error";
            msgEl.textContent = `✗ ${data.detail || "Error"}`;
        }
    } catch (e) {
        msgEl.className = "action-msg error";
        msgEl.textContent = `✗ Network error: ${e.message}`;
    }

    setTimeout(() => { msgEl.classList.add("hidden"); }, 5000);
}

document.getElementById("sync-btn").addEventListener("click",
    () => triggerAction("/api/data/sync", "Data sync"));

document.getElementById("features-btn").addEventListener("click",
    () => triggerAction("/api/data/compute-features", "Feature computation"));

document.getElementById("train-btn").addEventListener("click",
    () => triggerAction("/api/data/train", "Model training"));

// ─────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────

function escHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-GB", {
        day: "2-digit", month: "short", year: "numeric",
    });
}
