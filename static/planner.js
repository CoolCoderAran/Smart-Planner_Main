let editingTaskId = null;
let currentFilter = "all";
const selectedTaskIds = new Set();

// Extract CSRF Token safely from meta tags
function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

// Universal API fetch wrapper
async function apiRequest(url, method = "GET", payload = null) {
    const options = {
        method,
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCsrfToken(),
            "X-Requested-With": "XMLHttpRequest"
        }
    };
    if (payload !== null) options.body = JSON.stringify(payload);

    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed.");
    return data;
}

function toggleSidebar() {
    document.getElementById("sidebar")?.classList.toggle("open");
    document.getElementById("sidebarOverlay")?.classList.toggle("open");
}

function openModal() {
    editingTaskId = null;
    const modal = document.getElementById("taskModal");
    if (!modal) return;
    
    document.getElementById("modalHeaderTitle").textContent = "Add New Task";
    document.getElementById("addTaskForm")?.reset();
    document.getElementById("modalTaskId").value = "";
    document.getElementById("modalPriority").value = "2";
    document.getElementById("modalDueTime").value = "23:59";
    
    modal.classList.add("active", "open");
    modal.style.display = "flex";
}

function closeModal() {
    const modal = document.getElementById("taskModal");
    if (modal) {
        modal.classList.remove("active", "open");
        modal.style.display = "none";
    }
    editingTaskId = null;
}

// Safe Task Editing trigger invoked by button click
function editTask(id, title, subject, minutes, priority, dueDate, dueTime, notes = "") {
    editingTaskId = id;
    const modal = document.getElementById("taskModal");
    if (!modal) return;

    document.getElementById("modalHeaderTitle").textContent = "Edit Task";
    document.getElementById("modalTaskId").value = id ?? "";
    document.getElementById("modalTaskName").value = title ?? "";
    document.getElementById("modalSubject").value = subject ?? "";
    document.getElementById("modalEstMinutes").value = minutes ?? 30;
    document.getElementById("modalPriority").value = String(priority ?? 2);
    document.getElementById("modalDueDate").value = dueDate ?? "";
    document.getElementById("modalDueTime").value = dueTime || "23:59";
    document.getElementById("modalNotes").value = notes ?? "";

    modal.classList.add("active", "open");
    modal.style.display = "flex";
}

async function toggleTask(id) {
    try {
        await apiRequest(`/planner/toggle/${id}`, "POST");
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

async function deleteTask(id) {
    if (!confirm("Are you sure you want to delete this task and its subtasks?")) return;
    try {
        await apiRequest(`/planner/delete/${id}`, "DELETE");
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

function toggleInlineSubtaskForm(parentId) {
    const form = document.getElementById(`inlineSubtaskForm-${parentId}`);
    if (form) {
        form.classList.toggle("active");
        if (form.classList.contains("active")) {
            document.getElementById(`inlineSubtaskInput-${parentId}`)?.focus();
        }
    }
}

function handleInlineSubtaskKey(event, parentId) {
    if (event.key === "Enter") {
        event.preventDefault();
        submitInlineSubtask(parentId);
    }
}

async function submitInlineSubtask(parentId) {
    const input = document.getElementById(`inlineSubtaskInput-${parentId}`);
    const title = input?.value?.trim();
    if (!title) return;

    try {
        await apiRequest("/planner/subtask/add", "POST", {
            parent_id: parentId,
            title
        });
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

function toggleTaskSelection(taskId, checkbox) {
    if (checkbox.checked) selectedTaskIds.add(taskId);
    else selectedTaskIds.delete(taskId);
    
    const count = document.getElementById("selectedCount");
    const bar = document.getElementById("batchBar");
    
    if (count) count.textContent = `${selectedTaskIds.size} task${selectedTaskIds.size === 1 ? "" : "s"} selected`;
    if (bar) bar.style.display = selectedTaskIds.size ? "flex" : "none";
}

async function runBatch(action) {
    if (!selectedTaskIds.size) return;
    try {
        await apiRequest("/planner/batch", "POST", {
            action,
            task_ids: Array.from(selectedTaskIds)
        });
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

function batchSetStatus(status) { if (status === "completed") runBatch("complete"); }
function batchSetDate(targetDate) { if (targetDate === "today") runBatch("shift_today"); }
function batchDelete() { if (confirm("Delete all selected tasks?")) runBatch("delete"); }

function sortContainerCards(container) {
    const cards = Array.from(container.children).filter(el => el.classList.contains("task-card"));
    
    cards.sort((a, b) => {
        const pA = Number(a.dataset.priority) || 3;
        const pB = Number(b.dataset.priority) || 3;
        if (pA !== pB) return pA - pB;

        const dueA = a.dataset.due || "9999-99-99";
        const dueB = b.dataset.due || "9999-99-99";
        return dueA.localeCompare(dueB);
    });

    cards.forEach(card => container.appendChild(card));
}

function applyFilters() {
    const query = (document.getElementById("search")?.value || "").toLowerCase().trim();

    document.querySelectorAll(".task-section").forEach(section => {
        const matchesSection = currentFilter === "all" || section.dataset.section === currentFilter;
        let visibleCards = 0;
        const listContainer = section.querySelector(".task-list");

        section.querySelectorAll(".task-card").forEach(card => {
            const matchesCat = currentFilter === "all" || card.dataset.category === currentFilter || section.dataset.section === currentFilter;
            const text = card.textContent.toLowerCase();
            const matchesSearch = !query || text.includes(query);
            
            const isVisible = matchesCat && matchesSearch;
            card.style.display = isVisible ? "" : "none";
            if (isVisible) visibleCards++;
        });

        if (listContainer) sortContainerCards(listContainer);
        section.style.display = matchesSection || visibleCards > 0 ? "" : "none";
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const today = new Date();
    const dateEl = document.getElementById("current-date");
    if (dateEl) {
        dateEl.textContent = today.toLocaleDateString("en-US", {
            weekday: "long", year: "numeric", month: "long", day: "numeric"
        });
    }

    document.querySelectorAll(".filter-btn, .sidebar-filter-link").forEach(btn => {
        btn.addEventListener("click", e => {
            e.preventDefault();
            currentFilter = btn.dataset.filter || "all";
            document.querySelectorAll(".filter-btn, .sidebar-filter-link").forEach(b => {
                b.classList.toggle("active", b.dataset.filter === currentFilter);
            });
            applyFilters();
        });
    });

    document.getElementById("search")?.addEventListener("keydown", async e => {
        if (e.key === "Enter" && e.target.value.includes("p")) {
            e.preventDefault();
            const val = e.target.value.trim();
            if (!val) return;

            try {
                await apiRequest("/planner/add", "POST", { title: val });
                e.target.value = "";
                window.location.reload();
            } catch (err) {
                alert(err.message);
            }
        }
    });

    document.getElementById("search")?.addEventListener("input", applyFilters);

    document.getElementById("addTaskForm")?.addEventListener("submit", async event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const payload = {
            title: form.get("title"),
            subject: form.get("subject"),
            due_date: form.get("due_date"),
            due_time: form.get("due_time"),
            estimated_minutes: Number(form.get("estimated_minutes") || 30),
            priority: Number(form.get("priority") || 2),
            notes: String(form.get("notes") || "").trim()
        };

        try {
            if (editingTaskId) {
                await apiRequest(`/planner/update/${editingTaskId}`, "POST", payload);
            } else {
                await apiRequest("/planner/add", "POST", payload);
            }
            window.location.reload();
        } catch (error) {
            alert(error.message);
        }
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") closeModal();
    });
    
    applyFilters();
});
