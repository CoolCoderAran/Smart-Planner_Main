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

function updateButtonUI() {
    const btnPrimary = document.getElementById("btnPrimary");
    const btnPause = document.getElementById("btnPause");
    const btnComplete = document.getElementById("btnComplete");
    const btnEnd = document.getElementById("btnEnd");

    if (!btnPrimary || !btnPause || !btnEnd) return;

    if (!isRunning && !isPaused) {
        // Idle State
        btnPrimary.style.display = "inline-block";
        btnPrimary.innerText = "Start Focus";
        btnPause.style.display = "none";
        if (btnComplete) btnComplete.style.display = "none";
        btnEnd.style.display = "none";
    } else if (isRunning) {
        // Active Running State
        btnPrimary.style.display = "none";
        btnPause.style.display = "inline-block";
        btnPause.innerText = "Pause";
        btnPause.disabled = false;
        if (btnComplete) btnComplete.style.display = "inline-block";
        btnEnd.style.display = "inline-block";
        btnEnd.innerText = "Abandon";
    } else if (isPaused) {
        // Paused State
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

        tickTimer();

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
