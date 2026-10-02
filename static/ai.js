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


