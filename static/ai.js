const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
let pendingTaskProposal = null;

function postJson(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
            "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify(payload)
    }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || data.message || "Request failed.");
        return data;
    });
}

function appendMessage(text, sender = "terra") {
    const container = document.getElementById("chatMessages");
    const message = document.createElement("div");
    message.className = `message ${sender === "user" ? "user-message" : "terra-message"}`;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.textContent = String(text || "");
    message.appendChild(bubble);
    container.appendChild(message);
    container.scrollTop = container.scrollHeight;
}

let pendingSchedule = [];

function renderProposals(data) {
    const container = document.getElementById("proposalContainer");
    const actions = document.getElementById("proposalActions");
    container.replaceChildren();
    pendingTaskProposal = data.proposed_task || null;
    pendingSchedule = data.proposed_schedule || [];

    if (!pendingTaskProposal && !(data.proposed_schedule || []).length) {
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

    const schedule = data.proposed_schedule || [];
    if (schedule.length) {
        const heading = document.createElement("h4");
        heading.textContent = `Proposed schedule (${schedule.length} block${schedule.length === 1 ? "" : "s"})`;
        container.appendChild(heading);

        schedule.forEach(block => {
            const item = document.createElement("p");
            item.textContent =
                `${block.title || "Study block"} — ${block.date} ${block.start_time}-${block.end_time}`;
            container.appendChild(item);
        });
    }
}

async function handleChatSubmit(event) {
    event.preventDefault();
    const input = document.getElementById("userInput");
    const sendBtn = document.getElementById("sendBtn");
    const query = input.value.trim();
    if (!query) return;

    appendMessage(query, "user");
    input.value = "";
    input.disabled = true;
    sendBtn.disabled = true;

    try {
        const data = await postJson("/api/ai/chat", { query });
        appendMessage(data.message || "Terra returned no message.", "terra");
        if (data.suggested_actions?.length) {
            appendMessage(`Suggested steps: ${data.suggested_actions.join(" • ")}`, "terra");
        }
        renderProposals(data);
    } catch (error) {
        appendMessage(error.message, "terra");
    } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
    }
}

function usePrompt(prompt) {
    const input = document.getElementById("userInput");
    input.value = prompt;
    input.focus();
}

function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        document.getElementById("aiForm").requestSubmit();
    }
}

function clearChatHistory() {
    const container = document.getElementById("chatMessages");
    container.querySelectorAll(".message:not(:first-child)").forEach(node => node.remove());
    document.getElementById("proposalContainer").replaceChildren();
    pendingTaskProposal = null;
    pendingSchedule = [];
    document.getElementById("proposalActions").style.display = "none";
}

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
        appendMessage(`Done — ${confirmations.join(" and ")}.`, "terra");
        pendingTaskProposal = null;
        pendingSchedule = [];
        renderProposals({});
    } catch (error) {
        appendMessage(error.message, "terra");
    } finally {
        if (applyBtn) applyBtn.disabled = false;
    }
}

function renderTerraMessage(data) {
    const chatMessages = document.getElementById("chatMessages");
    if (!chatMessages) return;

    const msgDiv = document.createElement("div");
    msgDiv.className = "message terra-message";

    let chipsHtml = "";
    if (Array.isArray(data.suggested_actions) && data.suggested_actions.length > 0) {
        chipsHtml = `<div class="suggestion-chips">` + 
            data.suggested_actions.map(act => {
                const label = typeof act === 'object' ? (act.label || act.action || 'Action') : act;
                return `<button type="button" class="chip" onclick="usePrompt('${label}')">${label}</button>`;
            }).join('') + 
        `</div>`;
    }

    msgDiv.innerHTML = `
        <div class="msg-avatar"><i data-lucide="sparkles"></i></div>
        <div class="msg-bubble">
            <p>${data.message}</p>
            ${chipsHtml}
        </div>
    `;

    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (window.lucide) {
        lucide.createIcons();
    }
}

// ==========================================
// TERRA AI ASSISTANT FRONTEND CONTROLLER
// Handles persistence via localStorage
// ==========================================

const CHAT_STORAGE_KEY = "terra_chat_history";

/**
 * Retrieves the current array of saved message objects from localStorage.
 */
function getStoredMessages() {
    try {
        const raw = localStorage.getItem(CHAT_STORAGE_KEY);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        console.error("Failed to parse chat history from localStorage:", e);
        return [];
    }
}

/**
 * Appends a new message object to localStorage history.
 */
function appendToStoredHistory(msgObj) {
    const history = getStoredMessages();
    history.push(msgObj);
    try {
        localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(history));
    } catch (e) {
        console.error("Failed to save chat message to localStorage:", e);
    }
}

/**
 * Clears saved chat history from localStorage and resets the chat UI.
 */
function clearChatHistory() {
    if (confirm("Are you sure you want to clear your chat history with Terra?")) {
        localStorage.removeItem(CHAT_STORAGE_KEY);
        const chatContainer = document.getElementById("chatMessages");
        if (chatContainer) {
            // Restore initial default Terra welcome bubble
            chatContainer.innerHTML = `
                <div class="message terra-message">
                    <div class="msg-avatar"><i data-lucide="sparkles"></i></div>
                    <div class="msg-bubble">
                        <p>Hello! I'm <strong>Terra</strong>, your AI scheduling assistant.</p>
                        <p>What are we planning today?</p>
                        <div class="suggestion-chips">
                            <button type="button" class="chip" onclick="usePrompt('Help me plan my study schedule for final exams next week.')">📚 Plan exam schedule</button>
                            <button type="button" class="chip" onclick="usePrompt('Break down my term paper into subtasks with deadlines.')">📝 Break down assignment</button>
                            <button type="button" class="chip" onclick="usePrompt('Organize my tasks for today by priority.')">⚡ Optimize today's schedule</button>
                        </div>
                    </div>
                </div>
            `;
        }
        if (window.lucide) lucide.createIcons();
    }
}

/**
 * Restores saved chat bubbles from localStorage on page load.
 */
function restoreChatHistoryUI() {
    const history = getStoredMessages();
    if (!history || history.length === 0) return;

    const chatContainer = document.getElementById("chatMessages");
    if (!chatContainer) return;

    // Clear static HTML default welcome if saved history exists
    chatContainer.innerHTML = "";

    history.forEach(item => {
        if (item.sender === "user") {
            appendUserBubbleUI(item.text, false); // false = don't re-save
        } else if (item.sender === "terra") {
            appendTerraBubbleUI(item.data, false); // false = don't re-save
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

    let chipsHtml = "";
    if (Array.isArray(data.suggested_actions) && data.suggested_actions.length > 0) {
        chipsHtml = `<div class="suggestion-chips">` + 
            data.suggested_actions.map(act => {
                const label = typeof act === 'object' ? (act.label || act.action || 'Action') : act;
                return `<button type="button" class="chip" onclick="usePrompt('${escapeHtml(label)}')">${escapeHtml(label)}</button>`;
            }).join('') + 
        `</div>`;
    }

    msgDiv.innerHTML = `
        <div class="msg-avatar"><i data-lucide="sparkles"></i></div>
        <div class="msg-bubble">
            <p>${escapeHtml(data.message)}</p>
            ${chipsHtml}
        </div>
    `;

    chatContainer.appendChild(msgDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    if (saveToStorage) {
        appendToStoredHistory({ sender: "terra", data: data });
    }
    if (window.lucide) lucide.createIcons();
}

/**
 * Submits user query to Flask backend via AJAX.
 */
async function handleChatSubmit(event) {
    if (event) event.preventDefault();

    const inputArea = document.getElementById("userInput");
    if (!inputArea) return;

    const query = inputArea.value.trim();
    if (!query) return;

    // Display and save user message
    appendUserBubbleUI(query, true);
    inputArea.value = "";

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    try {
        const response = await fetch("/api/ai/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken,
                "X-Requested-With": "XMLHttpRequest"
            },
            body: JSON.stringify({ query: query })
        });

        const data = await response.json();
        if (!response.ok) {
            appendTerraBubbleUI({ message: data.error || "An error occurred while processing your request.", suggested_actions: [] }, true);
            return;
        }

        // Display and save Terra response
        appendTerraBubbleUI(data, true);

        // Render proposals if returned in payload
        if (typeof renderProposals === "function") {
            renderProposals(data);
        }

    } catch (err) {
        console.error("Chat request failed:", err);
        appendTerraBubbleUI({ message: "Could not connect to Terra AI. Please check your connection.", suggested_actions: [] }, true);
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

function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
    restoreChatHistoryUI();
});
