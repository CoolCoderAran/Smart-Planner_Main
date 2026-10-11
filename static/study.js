// Configuration presets for timer modes
const MODES = {
    pomodoro: { focus: 25, break: 5 },
    deepwork: { focus: 90, break: 15 },
    quickburst: { focus: 15, break: 3 },
    examcrunch: { focus: 50, break: 10 }
};

// Application State Variables
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

// CSRF Token for Secure POST Requests
const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

/**
 * Helper function to send POST requests with JSON payloads.
 */
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
        const val = parseInt(document.getElementById("customFocus")?.value, 10);
        return isNaN(val) || val < 1 ? 25 : val;
    }
    return MODES[currentMode]?.focus || 25;
}

function getBreakMinutes() {
    if (currentMode === "custom") {
        const val = parseInt(document.getElementById("customBreak")?.value, 10);
        return isNaN(val) || val < 1 ? 5 : val;
    }
    return MODES[currentMode]?.break || 5;
}

/**
 * Updates button visibility and labels based on timer state.
 */
function updateButtonUI() {
    const btnPrimary = document.getElementById("btnPrimary");
    const btnPause = document.getElementById("btnPause");
    const btnComplete = document.getElementById("btnComplete");
    const btnEnd = document.getElementById("btnEnd");

    if (!btnPrimary || !btnPause || !btnEnd) return;

    if (!isRunning && !isPaused) {
        // IDLE STATE
        btnPrimary.style.display = "inline-block";
        btnPrimary.innerText = "Start Focus";
        btnPause.style.display = "none";
        if (btnComplete) btnComplete.style.display = "none";
        btnEnd.style.display = "none";
    } else if (isRunning) {
        // ACTIVE RUNNING STATE
        btnPrimary.style.display = "none";
        btnPause.style.display = "inline-block";
        btnPause.innerText = "Pause";
        btnPause.disabled = false;
        if (btnComplete) btnComplete.style.display = "inline-block";
        btnEnd.style.display = "inline-block";
        btnEnd.innerText = "Abandon";
    } else if (isPaused) {
        // PAUSED STATE
        btnPrimary.style.display = "inline-block";
        btnPrimary.innerText = "Resume";
        btnPause.style.display = "inline-block";
        btnPause.innerText = "Paused";
        btnPause.disabled = true;
        if (btnComplete) btnComplete.style.display = "inline-block";
        btnEnd.style.display = "inline-block";
        btnEnd.innerText = "Abandon";
    }
}

function handlePrimaryAction() {
    if (isPaused) {
        resumeTimer();
    } else if (!isRunning) {
        startTimer();
    }
}

function loadMode() {
    if (isRunning || activeSessionId) return;

    const modeSelect = document.getElementById("modeSelect");
    if (modeSelect) currentMode = modeSelect.value;

    const customSettings = document.getElementById("customSettings");
    if (customSettings) {
        customSettings.style.display = currentMode === "custom" ? "grid" : "none";
    }

    isBreak = false;
    secondsRemaining = getFocusMinutes() * 60;
    const phaseLabel = document.getElementById("phaseLabel");
    if (phaseLabel) phaseLabel.innerText = "Focus Time";
    updateDisplay();
    updateButtonUI();
}

function updateDisplay() {
    const display = document.getElementById("timerDisplay");
    if (!display) return;
    
    const minutes = Math.floor(Math.max(0, secondsRemaining) / 60);
    const seconds = Math.max(0, secondsRemaining) % 60;
    display.innerText = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function tickTimer() {
    if (!isBreak && activeStartedAt) {
        const startTime = new Date(activeStartedAt).getTime();
        if (isNaN(startTime)) {
            secondsRemaining--;
        } else {
            let pausedOffset = activeTotalPausedSeconds;
            if (activePausedAt) {
                pausedOffset += Math.floor((Date.now() - new Date(activePausedAt).getTime()) / 1000);
            }
            const elapsedRaw = Math.floor((Date.now() - startTime) / 1000);
            const elapsed = Math.max(0, elapsedRaw - pausedOffset);
            secondsRemaining = Math.max(0, (getFocusMinutes() * 60) - elapsed);
        }
    } else {
        secondsRemaining--;
    }

    updateDisplay();

    if (secondsRemaining <= 0) {
        clearInterval(timer);
        isRunning = false;
        if (!isBreak) {
            sessionFinished();
        } else {
            switchPhase();
        }
    }
}

async function startTimer() {
    if (isRunning) return;

    try {
        if (!isBreak && !activeSessionId) {
            const data = await postJSON("/focus/start", {
                task_id: document.getElementById("taskSelect")?.value || null,
                mode: currentMode
            });
            activeSessionId = data.session_id;
            activeStartedAt = data.started_at;
            activeTotalPausedSeconds = 0;
            activePausedAt = null;
        }

        isRunning = true;
        isPaused = false;
        updateButtonUI();

        tickTimer(); // Immediate render
        clearInterval(timer);
        timer = setInterval(tickTimer, 1000);
    } catch (error) {
        alert(error.message || "Failed to start timer session.");
    }
}

async function pauseTimer() {
    if (!isRunning) return;
    clearInterval(timer);
    isRunning = false;
    isPaused = true;
    updateButtonUI();
    
    if (!activeSessionId) return;
    try {
        const data = await postJSON("/focus/pause", { session_id: activeSessionId });
        activePausedAt = data.paused_at;
    } catch (error) {
        console.error("Could not pause focus session on server:", error);
    }
}

async function resumeTimer() {
    if (isRunning) return;
    if (activeSessionId && isPaused) {
        try {
            const data = await postJSON("/focus/resume", { session_id: activeSessionId });
            activeTotalPausedSeconds = data.total_paused_seconds || 0;
            activePausedAt = null;
        } catch (error) {
            console.error("Could not resume focus session on server:", error);
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
    
    const phaseLabel = document.getElementById("phaseLabel");
    if (phaseLabel) phaseLabel.innerText = "Focus Time";
    updateDisplay();
    updateButtonUI();
}

async function completeSessionEarly() {
    if (!activeSessionId) return;
    
    if (confirm("Would you like to complete and log this focus session now?")) {
        clearInterval(timer);
        isRunning = false;
        isPaused = false;
        await sessionFinished();
    }
}

async function sessionFinished() {
    if (!activeSessionId) return;

    try {
        const response = await postJSON("/focus/complete", {
            session_id: activeSessionId,
            note: document.getElementById("brainDumpNotes")?.value || ""
        });

        if (response.success) {
            console.log("Focus session successfully logged:", response);
            await loadStats();
            await loadHistory();
        }
    } catch (error) {
        console.error("Failed to log focus session:", error);
        alert("Could not log session: " + error.message);
    } finally {
        activeSessionId = null;
        activeStartedAt = null;
        activeTotalPausedSeconds = 0;
        activePausedAt = null;
        isPaused = false;
        switchPhase();
        updateButtonUI();
    }
}

function switchPhase() {
    const phaseLabel = document.getElementById("phaseLabel");
    if (!isBreak) {
        if (phaseLabel) phaseLabel.innerText = "Break Time";
        secondsRemaining = getBreakMinutes() * 60;
        isBreak = true;
    } else {
        isBreak = false;
        if (phaseLabel) phaseLabel.innerText = "Focus Time";
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
        currentMode = data.active.mode || "pomodoro";
        
        const modeSelect = document.getElementById("modeSelect");
        if (modeSelect) modeSelect.value = currentMode;

        let pausedSoFar = activeTotalPausedSeconds;
        if (activePausedAt) {
            pausedSoFar += Math.floor((Date.now() - new Date(activePausedAt).getTime()) / 1000);
        }
        
        const startTime = new Date(activeStartedAt).getTime();
        const elapsed = Math.max(0, Math.floor((Date.now() - startTime) / 1000) - pausedSoFar);
        secondsRemaining = Math.max(0, (getFocusMinutes() * 60) - elapsed);
        
        const phaseLabel = document.getElementById("phaseLabel");
        if (phaseLabel) phaseLabel.innerText = "Focus Time";
        updateDisplay();

        if (secondsRemaining <= 0) {
            await sessionFinished();
        } else if (activePausedAt) {
            isRunning = false;
            isPaused = true;
            updateButtonUI();
        } else {
            startTimer();
        }
    } catch (error) {
        console.error("Could not restore active focus session.", error);
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

    loadFocusNotes();

    notesElem.addEventListener("input", () => {
        clearTimeout(_notesSaveTimer);
        _notesSaveTimer = setTimeout(() => saveFocusNotes(notesElem.value), 800);
    });
}

let activeAudio = null;
let activeBtn = null;

function toggleAudio(btnElement, type) {
    const audioMap = {
        rain: "audioRain", lofi: "audioLofi", waves: "audioWaves",
        forest: "audioForest", cafe: "audioCafe", fire: "audioFire"
    };
    const audio = document.getElementById(audioMap[type]);
    if (!audio) return;

    if (activeAudio === audio) {
        audio.pause();
        activeBtn?.classList.remove("playing");
        activeAudio = activeBtn = null;
        return;
    }
    
    if (activeAudio) activeAudio.pause();
    if (activeBtn) activeBtn.classList.remove("playing");
    
    audio.play().catch(() => {});
    btnElement.classList.add("playing");
    activeAudio = audio;
    activeBtn = btnElement;
}

async function loadHistory() {
    try {
        const response = await fetch("/get_recent_sessions");
        if (!response.ok) return;
        const sessions = await response.json();
        const tbody = document.getElementById("sessionHistoryBody");
        if (!tbody) return;

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

// Global Event Initialization
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("modeSelect")?.addEventListener("change", loadMode);
    document.getElementById("customFocus")?.addEventListener("input", loadMode);
    document.getElementById("customBreak")?.addEventListener("input", loadMode);

    loadMode();
    loadStats();
    loadHistory();
    restoreActiveSession();
    initFocusNotes();
});
```[cite: 7]

---

### Instructions to Implement

1. Copy the code block above into `static/study.js`[cite: 7].
2. Save the file and perform a hard refresh in your browser (`Ctrl + Shift + R` or `Cmd + Shift + R`).
3. Click **Start Focus**, wait a few seconds, then click **Complete**: your session will instantly log into the SQLite database and update the history table[cite: 4, 5, 7]!
