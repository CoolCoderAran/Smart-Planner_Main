const MODES = {
    pomodoro: { focus: 25, break: 5 },
    deepwork: { focus: 90, break: 15 },
    quickburst: { focus: 15, break: 3 },
    examcrunch: { focus: 50, break: 10 }
};

let timer = null;
let secondsRemaining = 1500;
let currentMode = "pomodoro";
let isBreak = false;
let isRunning = false;
let isPaused = false;
let activeSessionId = null;
let activeStartedAt = null;
let activeTotalPausedSeconds = 0;
let activePausedAt = null;

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

function postJSON(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
            "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify(payload)
    }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Request failed.");
        return data;
    });
}

function getFocusMinutes() {
    if (currentMode === "custom") {
        return Math.max(1, parseInt(document.getElementById("customFocus").value, 10) || 25);
    }
    return MODES[currentMode].focus;
}

function getBreakMinutes() {
    if (currentMode === "custom") {
        return Math.max(1, parseInt(document.getElementById("customBreak").value, 10) || 5);
    }
    return MODES[currentMode].break;
}

function loadMode() {
    currentMode = document.getElementById("modeSelect").value;
    document.getElementById("customSettings").style.display =
        currentMode === "custom" ? "grid" : "none";

    if (!isRunning && !activeSessionId) {
        isBreak = false;
        secondsRemaining = getFocusMinutes() * 60;
        document.getElementById("phaseLabel").innerText = "Focus Time";
        updateDisplay();
    }
}

function updateDisplay() {
    const minutes = Math.floor(Math.max(0, secondsRemaining) / 60);
    const seconds = Math.max(0, secondsRemaining) % 60;
    document.getElementById("timerDisplay").innerText =
        `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

async function startTimer() {
    if (isRunning || isBreak) return;

    try {
        if (!activeSessionId) {
            const data = await postJSON("/focus/start", {
                task_id: document.getElementById("taskSelect").value || null,
                mode: currentMode
            });
            activeSessionId = data.session_id;
            activeStartedAt = data.started_at;
            activeTotalPausedSeconds = 0;
            activePausedAt = null;
        }
        isRunning = true;
        isPaused = false;
        clearInterval(timer);
        timer = setInterval(() => {
            if (!activeStartedAt) return;
            const elapsedRaw = Math.floor(
                (Date.now() - new Date(activeStartedAt).getTime()) / 1000
            );
            const elapsed = Math.max(0, elapsedRaw - activeTotalPausedSeconds);
            secondsRemaining = Math.max(0, getFocusMinutes() * 60 - elapsed);
            updateDisplay();
            if (secondsRemaining <= 0) {
                clearInterval(timer);
                isRunning = false;
                sessionFinished();
            }
        }, 500);
    } catch (error) {
        alert(error.message);
    }
}

async function pauseTimer() {
    if (!isRunning) return;
    clearInterval(timer);
    isRunning = false;
    isPaused = true;
    if (!activeSessionId) return;
    try {
        const data = await postJSON("/focus/pause", { session_id: activeSessionId });
        activePausedAt = data.paused_at;
    } catch (error) {
        console.error("Could not pause focus session on the server:", error);
    }
}

async function resumeTimer() {
    if (isRunning) return;
    if (activeSessionId && isPaused) {
        try {
            const data = await postJSON("/focus/resume", { session_id: activeSessionId });
            activeTotalPausedSeconds = data.total_paused_seconds;
            activePausedAt = null;
        } catch (error) {
            console.error("Could not resume focus session on the server:", error);
        }
    }
    isPaused = false;
    startTimer();
}

async function resetTimer() {
    clearInterval(timer);
    isRunning = false;
    isPaused = false;
    if (activeSessionId) {
        try {
            await postJSON("/focus/abandon", { session_id: activeSessionId });
        } catch (error) {
            console.error("Could not abandon focus session:", error);
        }
    }
    activeSessionId = null;
    activeStartedAt = null;
    activeTotalPausedSeconds = 0;
    activePausedAt = null;
    isBreak = false;
    secondsRemaining = getFocusMinutes() * 60;
    document.getElementById("phaseLabel").innerText = "Focus Time";
    updateDisplay();
}

async function sessionFinished() {
    if (!activeSessionId) return;

    try {
        const data = await postJSON("/focus/complete", {
            session_id: activeSessionId,
            note: document.getElementById("brainDumpNotes")?.value || ""
        });
        await loadStats();
        await loadHistory();
        if (data.already === "completed") {
            console.log("This Focus session was already completed elsewhere (e.g. another tab).");
        } else {
            console.log(`Recorded ${data.minutes} server-authoritative focus minutes.`);
        }
    } catch (error) {
        console.error("Failed to complete focus session:", error);
    } finally {
        activeSessionId = null;
        activeStartedAt = null;
        activeTotalPausedSeconds = 0;
        activePausedAt = null;
        isPaused = false;
        switchPhase();
    }
}

function switchPhase() {
    if (!isBreak) {
        document.getElementById("phaseLabel").innerText = "Break Time";
        secondsRemaining = getBreakMinutes() * 60;
        isBreak = true;
    } else {
        isBreak = false;
        document.getElementById("phaseLabel").innerText = "Focus Time";
        secondsRemaining = getFocusMinutes() * 60;
    }
    updateDisplay();
}

async function restoreActiveSession() {
    try {
        const response = await fetch("/focus/active");
        if (!response.ok) return;
        const data = await response.json();
        if (!data.active) return;

        activeSessionId = data.active.id;
        activeStartedAt = data.active.started_at;
        activeTotalPausedSeconds = data.active.total_paused_seconds || 0;
        activePausedAt = data.active.paused_at || null;
        currentMode = data.active.mode || "focus";
        if (!MODES[currentMode] && currentMode !== "custom") currentMode = "pomodoro";
        document.getElementById("modeSelect").value = currentMode;

        let pausedSoFar = activeTotalPausedSeconds;
        if (activePausedAt) {
            pausedSoFar += Math.floor((Date.now() - new Date(activePausedAt).getTime()) / 1000);
        }
        const elapsed = Math.max(0, Math.floor(
            (Date.now() - new Date(activeStartedAt).getTime()) / 1000
        ) - pausedSoFar);
        secondsRemaining = Math.max(0, getFocusMinutes() * 60 - elapsed);
        document.getElementById("phaseLabel").innerText = "Focus Time";
        updateDisplay();

        if (secondsRemaining <= 0) {
            await sessionFinished();
        } else if (activePausedAt) {
            isRunning = false;
            isPaused = true;
        } else {
            startTimer();
        }
    } catch (error) {
        console.error("Could not restore active Focus session.", error);
    }
}

async function loadStats() {
    try {
        const response = await fetch("/get_study_stats");
        if (!response.ok) return;
        const data = await response.json();

        const today = data.today_minutes || 0;
        document.getElementById("todayMinutes").innerText = today;
        document.getElementById("weeklyMinutes").innerText = data.weekly_minutes || 0;
        document.getElementById("totalSessions").innerText = data.total_sessions || 0;
        document.getElementById("totalMinutes").innerText = data.total_minutes || 0;
        document.getElementById("currentStreak").innerText = `${data.current_streak || 0} Days`;
        document.getElementById("bestStreak").innerText = `${data.best_streak || 0} Days`;

        const target = 120;
        const pct = Math.min(100, Math.round((today / target) * 100));
        document.getElementById("goalFill").style.width = `${pct}%`;
        document.getElementById("goalProgressText").innerText = `${today} / ${target} mins`;
    } catch (error) {
        console.error("Could not load stats.", error);
    }
}

function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(content => content.classList.remove("active"));
    const index = tabName === "history" ? 0 : 1;
    document.querySelectorAll(".tab-btn")[index]?.classList.add("active");
    document.getElementById(tabName === "history" ? "tabHistory" : "tabNotes")?.classList.add("active");
}

// ---------------------------------------------------------------------------
// Focus notes — server-side persistence (T150 / T151 / T152)
// ---------------------------------------------------------------------------

let _notesSaveTimer = null;

async function loadFocusNotes() {
    const notesElem = document.getElementById("brainDumpNotes");
    if (!notesElem) return;
    try {
        const response = await fetch("/focus/notes");
        if (!response.ok) return;
        const data = await response.json();
        notesElem.value = data.note || "";
    } catch (error) {
        // Fall back silently — the textarea stays blank.
        console.error("Could not load focus notes:", error);
    }
}

async function saveFocusNotes(text) {
    try {
        await postJSON("/focus/notes", { note: text });
    } catch (error) {
        console.error("Could not save focus notes:", error);
    }
}

function initFocusNotes() {
    const notesElem = document.getElementById("brainDumpNotes");
    if (!notesElem) return;

    // Load persisted notes from the server on page load.
    loadFocusNotes();

    // Debounce saves: wait 800 ms of inactivity before hitting the server.
    notesElem.addEventListener("input", () => {
        clearTimeout(_notesSaveTimer);
        _notesSaveTimer = setTimeout(() => saveFocusNotes(notesElem.value), 800);
    });
}

// ---------------------------------------------------------------------------
// Ambient audio
// ---------------------------------------------------------------------------

let activeAudio = null;
let activeBtn = null;

function toggleAudio(type) {
    const audio = document.getElementById({
        rain: "audioRain", lofi: "audioLofi", waves: "audioWaves",
        forest: "audioForest", cafe: "audioCafe", fire: "audioFire"
    }[type]);
    const btn = event.currentTarget;
    if (!audio) return;

    if (activeAudio === audio) {
        audio.pause();
        activeBtn?.classList.remove("playing");
        activeAudio = activeBtn = null;
        return;
    }
    activeAudio?.pause();
    activeBtn?.classList.remove("playing");
    audio.play().catch(() => {});
    btn.classList.add("playing");
    activeAudio = audio;
    activeBtn = btn;
}

// ---------------------------------------------------------------------------
// Session history
// ---------------------------------------------------------------------------

async function loadHistory() {
    try {
        const response = await fetch("/get_recent_sessions");
        if (!response.ok) return;
        const sessions = await response.json();
        const tbody = document.getElementById("sessionHistoryBody");
        if (!sessions.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-msg">No recent sessions found.</td></tr>';
            return;
        }

        tbody.replaceChildren(...sessions.map(session => {
            const row = document.createElement("tr");
            [
                session.task || "General Focus",
                session.mode || "focus",
                `${session.minutes} mins`,
                session.note ? "Saved" : "None",
                session.completed_at ? new Date(session.completed_at).toLocaleDateString() : ""
            ].forEach(value => {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.appendChild(cell);
            });
            return row;
        }));
    } catch (error) {
        console.error("Could not fetch session history.", error);
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.getElementById("modeSelect")?.addEventListener("change", loadMode);
document.getElementById("customFocus")?.addEventListener("change", loadMode);
document.getElementById("customBreak")?.addEventListener("change", loadMode);

loadMode();
loadStats();
loadHistory();
restoreActiveSession();
initFocusNotes();



const MODES = {
    pomodoro: { focus: 25, break: 5 },
    deepwork: { focus: 90, break: 15 },
    quickburst: { focus: 15, break: 3 },
    examcrunch: { focus: 50, break: 10 }
};

let timer = null;
let secondsRemaining = 1500;
let currentMode = "pomodoro";
let isBreak = false;
let isRunning = false;

function loadMode() {
    currentMode = document.getElementById("modeSelect").value;

    if (currentMode === "custom") {
        document.getElementById("customSettings").style.display = "grid";
        const customMins = parseInt(document.getElementById("customFocus").value) || 25;
        secondsRemaining = customMins * 60;
    } else {
        document.getElementById("customSettings").style.display = "none";
        secondsRemaining = MODES[currentMode].focus * 60;
    }

    isBreak = false;
    document.getElementById("phaseLabel").innerText = "Focus Time";
    updateDisplay();
}

function updateDisplay() {
    const minutes = Math.floor(secondsRemaining / 60);
    const seconds = secondsRemaining % 60;
    document.getElementById("timerDisplay").innerText = 
        `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function startTimer() {
    if (isRunning) return;
    isRunning = true;

    timer = setInterval(() => {
        secondsRemaining--;
        updateDisplay();

        if (secondsRemaining <= 0) {
            clearInterval(timer);
            isRunning = false;
            sessionFinished();
        }
    }, 1000);
}

function pauseTimer() {
    clearInterval(timer);
    isRunning = false;
}

function resumeTimer() {
    startTimer();
}

function resetTimer() {
    clearInterval(timer);
    isRunning = false;
    loadMode();
}

async function sessionFinished() {
    let completedMinutes = 0;

    if (currentMode === "custom") {
        completedMinutes = parseInt(document.getElementById("customFocus").value) || 0;
    } else {
        completedMinutes = MODES[currentMode].focus;
    }

    const selectedTask = document.getElementById("taskSelect").value;

    if (!isBreak && completedMinutes > 0) {
        try {
            const res = await fetch("/save_study_session", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    mode: currentMode,
                    task: selectedTask || "General Focus",
                    minutes: completedMinutes
                })
            });

            const data = await res.json();
            if (data.success) {
                // Refresh both the stats counters and the history log table instantly
                await loadStats();
                await loadHistory();
            }
        } catch (err) {
            console.error("Failed to save study session:", err);
        }
    }

    switchPhase();
}






function switchPhase() {
    if (!isBreak) {
        document.getElementById("phaseLabel").innerText = "Break Time";
        if (currentMode === "custom") {
            const breakMins = parseInt(document.getElementById("customBreak").value) || 5;
            secondsRemaining = breakMins * 60;
        } else {
            secondsRemaining = MODES[currentMode].break * 60;
        }
        isBreak = true;
    } else {
        document.getElementById("phaseLabel").innerText = "Focus Time";
        loadMode();
        isBreak = false;
    }

    updateDisplay();
}

async function loadStats() {
    try {
        const response = await fetch("/get_study_stats");
        if (!response.ok) return;

        const data = await response.json();
        
        const today = data.today_minutes || 0;
        document.getElementById("todayMinutes").innerText = today;
        document.getElementById("weeklyMinutes").innerText = data.weekly_minutes || 0;
        document.getElementById("totalSessions").innerText = data.total_sessions || 0;
        document.getElementById("totalMinutes").innerText = data.total_minutes || 0;

        // Populate Streaks
        document.getElementById("currentStreak").innerText = `${data.current_streak || 0} Days`;
        document.getElementById("bestStreak").innerText = `${data.best_streak || 0} Days`;

        // Daily Goal progress update (Target: 120 mins)
        const target = 120;
        const pct = Math.min(100, Math.round((today / target) * 100));
        document.getElementById("goalFill").style.width = `${pct}%`;
        document.getElementById("goalProgressText").innerText = `${today} / ${target} mins`;
    } catch (e) {
        console.error("Could not load stats", e);
    }
}

// TAB SWITCHING
function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(content => content.classList.remove("active"));

    if (tabName === 'history') {
        document.querySelectorAll(".tab-btn")[0].classList.add("active");
        document.getElementById("tabHistory").classList.add("active");
    } else {
        document.querySelectorAll(".tab-btn")[1].classList.add("active");
        document.getElementById("tabNotes").classList.add("active");
    }
}

// BRAIN DUMP LOCALSTORAGE
const notesElem = document.getElementById("brainDumpNotes");
if (notesElem) {
    notesElem.value = localStorage.getItem("focus_notes") || "";
    notesElem.addEventListener("input", () => {
        localStorage.setItem("focus_notes", notesElem.value);
    });
}

// AMBIENT AUDIO CONTROLLER
let activeAudio = null;
let activeBtn = null;

function toggleAudio(type) {
    const soundMap = {
        rain: { audio: document.getElementById("audioRain"), btn: event.currentTarget },
        lofi: { audio: document.getElementById("audioLofi"), btn: event.currentTarget },
        waves: { audio: document.getElementById("audioWaves"), btn: event.currentTarget },
        forest: { audio: document.getElementById("audioForest"), btn: event.currentTarget },
        cafe: { audio: document.getElementById("audioCafe"), btn: event.currentTarget },
        fire: { audio: document.getElementById("audioFire"), btn: event.currentTarget }
    };

    const target = soundMap[type];

    if (activeAudio === target.audio) {
        activeAudio.pause();
        activeBtn.classList.remove("playing");
        activeAudio = null;
        activeBtn = null;
        return;
    }

    if (activeAudio) {
        activeAudio.pause();
        activeBtn.classList.remove("playing");
    }

    target.audio.play();
    target.btn.classList.add("playing");
    activeAudio = target.audio;
    activeBtn = target.btn;
}

// FETCH RECENT HISTORY
async function loadHistory() {
    try {
        const res = await fetch("/get_recent_sessions");
        if (!res.ok) return;
        const sessions = await res.json();
        
        const tbody = document.getElementById("sessionHistoryBody");
        if (sessions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty-msg">No recent sessions found.</td></tr>';
            return;
        }

        tbody.innerHTML = sessions.map(s => `
            <tr>
                <td>${s.task || 'General Focus'}</td>
                <td style="text-transform: capitalize;">${s.mode}</td>
                <td>${s.minutes} mins</td>
                <td>${new Date(s.completed_at).toLocaleDateString()}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error("Could not fetch session history", err);
    }
}

// Hook into initial load
loadHistory();

document.getElementById("modeSelect").addEventListener("change", loadMode);
document.getElementById("customFocus").addEventListener("change", loadMode);

loadMode();
loadStats();
