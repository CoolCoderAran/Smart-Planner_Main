// ==========================================
// TERRA AI ASSISTANT FRONTEND CONTROLLER
// Handles UI state, proposals, AJAX & user-scoped localStorage persistence
// ==========================================

function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

/**
 * Dynamically resolves a unique storage key per user so chat history
 * is never shared across different user accounts on the same browser.
 */
function getStorageKey() {
    const usernameEl = document.querySelector('.account-username') || document.querySelector('.username');
    const username = usernameEl ? usernameEl.textContent.trim().toLowerCase() : "default";
    return `terra_chat_history_${username}`;
}

let pendingTaskProposal = null;
let pendingSchedule = [];

/**
 * Helper to make JSON POST requests with CSRF headers.
 */
function postJson(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCsrfToken(),
            "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify(payload)
    }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || data.message || "Request failed.");
        return data;
    });
}

/**
 * Sanitizes input strings against XSS attacks.
 */
function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

/**
 * Retrieves saved chat messages for the active user from localStorage.
 */
function getStoredMessages() {
    try {
        const key = getStorageKey();
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        console.error("Failed to parse chat history from localStorage:", e);
        return [];
    }
}

/**
 * Appends a message object to the active user's localStorage history.
 */
function appendToStoredHistory(msgObj) {
    const history = getStoredMessages();
    history.push(msgObj);
    try {
        const key = getStorageKey();
        localStorage.setItem(key, JSON.stringify(history));
    } catch (e) {
        console.error("Failed to save chat message to localStorage:", e);
    }
}

/**
 * Clears saved chat history for the active user from localStorage and resets UI.
 */
function clearChatHistory() {
    if (confirm("Are you sure you want to clear your chat history with Terra?")) {
        const key = getStorageKey();
        localStorage.removeItem(key);
        const chatContainer = document.getElementById("chatMessages");
        if (chatContainer) {
            chatContainer.innerHTML = `
                <div class="message terra-message">
                    <div class="msg-avatar"><i data-lucide="sparkles"></i></div>
                    <div class="msg-bubble">
                        <p>Hello! I'm <strong>Terra</strong>, your AI scheduling assistant.</p>
                        <p>What are we planning today?</p>
                        <div class="suggestion-chips" id="defaultChips"></div>
                    </div>
                </div>
            `;
            
            const chipsContainer = chatContainer.querySelector("#defaultChips");
            const defaultPrompts = [
                { label: "📚 Plan exam schedule", query: "Help me plan my study schedule for final exams next week." },
                { label: "📝 Break down assignment", query: "Break down my term paper into subtasks with deadlines." },
                { label: "⚡ Optimize today's schedule", query: "Organize my tasks for today by priority." }
            ];

            defaultPrompts.forEach(p => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "chip";
                btn.textContent = p.label;
                btn.addEventListener("click", () => usePrompt(p.query));
                chipsContainer.appendChild(btn);
            });
        }
        renderProposals({});
        if (window.lucide) lucide.createIcons();
    }
}

/**
 * Restores saved chat bubbles from localStorage on page load for current user.
 */
function restoreChatHistoryUI() {
    const history = getStoredMessages();
    if (!history || history.length === 0) return;

    const chatContainer = document.getElementById("chatMessages");
    if (!chatContainer) return;

    // Clear existing server placeholder bubbles before restoring
    chatContainer.replaceChildren();

    history.forEach(item => {
        if (item.sender === "user") {
            appendUserBubbleUI(item.text, false);
        } else if (item.sender === "terra") {
            appendTerraBubbleUI(item.data, false);
        }
    });

    chatContainer.scrollTop = chatContainer.scrollHeight;
    if (window.lucide) lucide.createIcons();
}

/**
 * Appends a user prompt bubble to the DOM.
 */
function appendUserBubbleUI(text, saveToStorage = true) {
    const chatContainer = document.getElementById("chatMessages");
    if (!chatContainer) return;

    const msgDiv = document.createElement("div");
    msgDiv.className = "message user-message";
    msgDiv.innerHTML = `
        <div class="msg-avatar"><i data-lucide="user"></i></div>
        <div class="msg-bubble"><p>${escapeHtml(text)}</p></div>
    `;
    chatContainer.appendChild(msgDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    if (saveToStorage) {
        appendToStoredHistory({ sender: "user", text: text });
    }
    if (window.lucide) lucide.createIcons();
}

/**
 * Appends a Terra AI response bubble to the DOM.
 */
function appendTerraBubbleUI(data, saveToStorage = true) {
    const chatContainer = document.getElementById("chatMessages");
    if (!chatContainer) return;

    const msgDiv = document.createElement("div");
    msgDiv.className = "message terra-message";

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.innerHTML = `<i data-lucide="sparkles"></i>`;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";

    const p = document.createElement("p");
    p.textContent = data.message || "";
    bubble.appendChild(p);

    if (Array.isArray(data.suggested_actions) && data.suggested_actions.length > 0) {
        const chipsDiv = document.createElement("div");
        chipsDiv.className = "suggestion-chips";

        data.suggested_actions.forEach(act => {
            const label = typeof act === "object" ? (act.label || act.action || "Action") : act;
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "chip";
            btn.textContent = label;
            btn.addEventListener("click", () => usePrompt(label));
            chipsDiv.appendChild(btn);
        });

        bubble.appendChild(chipsDiv);
    }

    msgDiv.appendChild(avatar);
    msgDiv.appendChild(bubble);

    chatContainer.appendChild(msgDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    if (saveToStorage) {
        appendToStoredHistory({ sender: "terra", data: data });
    }
    if (window.lucide) lucide.createIcons();
}

/**
 * Renders proposed tasks or schedules generated by Terra.
 */
function renderProposals(data) {
    const container = document.getElementById("proposalContainer");
    const actions = document.getElementById("proposalActions");
    if (!container || !actions) return;

    container.replaceChildren();
    pendingTaskProposal = data.proposed_task || null;
    pendingSchedule = data.proposed_schedule || [];

    if (!pendingTaskProposal && !pendingSchedule.length) {
        const empty = document.createElement("div");
        empty.className = "empty-proposal-state";
        empty.textContent = "No active proposals.";
        container.appendChild(empty);
        actions.style.display = "none";
        return;
    }

    if (pendingTaskProposal) {
        const card = document.createElement("div");
        card.className = "proposal-item";

        const title = document.createElement("strong");
        title.textContent = pendingTaskProposal.title || "Untitled task";
        card.appendChild(title);

        const details = document.createElement("p");
        details.textContent =
            `Due ${pendingTaskProposal.due_date || "unscheduled"} · ` +
            `Priority ${pendingTaskProposal.priority ?? 2} · ` +
            `${pendingTaskProposal.estimated_minutes ?? 30} minutes`;
        card.appendChild(details);

        container.appendChild(card);
        actions.style.display = "block";
    }

    if (pendingSchedule.length) {
        const heading = document.createElement("h4");
        heading.textContent = `Proposed schedule (${pendingSchedule.length} block${pendingSchedule.length === 1 ? "" : "s"})`;
        container.appendChild(heading);

        pendingSchedule.forEach(block => {
            const item = document.createElement("p");
            item.textContent = `${block.title || "Study block"} — ${block.date} ${block.start_time}-${block.end_time}`;
            container.appendChild(item);
        });
        actions.style.display = "block";
    }
}

/**
 * Applies active task or schedule proposals to the planner database.
 */
async function applyAllProposals() {
    if (!pendingTaskProposal && !pendingSchedule.length) return;
    const applyBtn = document.querySelector('[onclick="applyAllProposals()"]');
    if (applyBtn) applyBtn.disabled = true;

    const confirmations = [];
    try {
        if (pendingTaskProposal) {
            await postJson("/planner/accept-proposal", pendingTaskProposal);
            confirmations.push("the proposed task was added to Planner");
        }
        if (pendingSchedule.length) {
            const result = await postJson("/api/calendar/apply-schedule", { blocks: pendingSchedule });
            confirmations.push(result.message || `${pendingSchedule.length} schedule block(s) were added to Calendar`);
        }
        appendTerraBubbleUI({ message: `Done — ${confirmations.join(" and ")}.`, suggested_actions: [] }, true);
        renderProposals({});
    } catch (error) {
        appendTerraBubbleUI({ message: error.message, suggested_actions: [] }, true);
    } finally {
        if (applyBtn) applyBtn.disabled = false;
    }
}

/**
 * Submits user query to Flask backend via AJAX.
 */
async function handleChatSubmit(event) {
    if (event) event.preventDefault();

    const inputArea = document.getElementById("userInput");
    const sendBtn = document.getElementById("sendBtn");
    if (!inputArea) return;

    const query = inputArea.value.trim();
    if (!query) return;

    appendUserBubbleUI(query, true);
    inputArea.value = "";
    inputArea.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    try {
        const data = await postJson("/api/ai/chat", { query: query });
        appendTerraBubbleUI(data, true);
        renderProposals(data);
    } catch (err) {
        console.error("Chat request failed:", err);
        appendTerraBubbleUI({ message: err.message || "Could not connect to Terra AI. Please check your connection.", suggested_actions: [] }, true);
    } finally {
        inputArea.disabled = false;
        if (sendBtn) sendBtn.disabled = false;
        inputArea.focus();
    }
}

function usePrompt(promptText) {
    const inputArea = document.getElementById("userInput");
    if (inputArea) {
        inputArea.value = promptText;
        handleChatSubmit();
    }
}

function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleChatSubmit(event);
    }
}

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
    restoreChatHistoryUI();
});
