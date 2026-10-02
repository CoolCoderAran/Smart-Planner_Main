let editingTaskId = null;
let currentFilter = "all";
const selectedTaskIds = new Set();
const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

function apiRequest(url, method = "GET", payload = null) {
    const options = {
        method,
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
            "X-Requested-With": "XMLHttpRequest"
        }
    };
    if (payload !== null) options.body = JSON.stringify(payload);

    return fetch(url, options).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Request failed.");
        return data;
    });
}

function toggleSidebar() {
    document.getElementById("sidebar")?.classList.toggle("open");
    document.getElementById("sidebarOverlay")?.classList.toggle("open");
}

function openModal() {
    editingTaskId = null;
    document.getElementById("modalHeaderTitle").textContent = "Add New Task";
    document.getElementById("addTaskForm").reset();
    document.getElementById("modalTaskId").value = "";
    document.getElementById("modalPriority").value = "2";
    document.getElementById("modalDueTime").value = "23:59";
    document.getElementById("taskModal").classList.add("active");
}

function closeModal() {
    document.getElementById("taskModal")?.classList.remove("active");
    editingTaskId = null;
}

function editTask(id, title, subject, minutes, priority, dueDate, dueTime = "") {
    editingTaskId = id;
    document.getElementById("modalHeaderTitle").textContent = "Edit Task";
    document.getElementById("modalTaskId").value = id;
    document.getElementById("modalTaskName").value = title;
    document.getElementById("modalSubject").value = subject || "";
    document.getElementById("modalEstMinutes").value = minutes || 30;
    document.getElementById("modalPriority").value = String(priority || 2);
    document.getElementById("modalDueDate").value = dueDate || "";
    document.getElementById("modalDueTime").value = dueTime || "23:59";
    document.getElementById("taskModal").classList.add("active");
}

async function toggleTask(id) {
    try {
        await apiRequest(`/planner/toggle/${id}`, "POST");
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

async function completeTask(id) {
    const card = document.querySelector(`[data-task-id="${id}"]`);
    const completed = card?.dataset.completed === "true";
    try {
        if (completed) {
            await apiRequest(`/planner/undo/${id}`, "POST");
        } else {
            await apiRequest(`/planner/complete/${id}`, "POST");
        }
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

async function deleteTask(id) {
    if (!confirm("Are you sure you want to delete this task?")) return;
    try {
        await apiRequest(`/planner/delete/${id}`, "DELETE");
        window.location.reload();
    } catch (error) {
        alert(error.message);
    }
}

async function openSubtaskModal(parentId) {
    const title = prompt("Enter subtask title:");
    if (!title || !title.trim()) return;
    try {
        await apiRequest("/planner/subtask/add", "POST", {
            parent_id: parentId,
            title: title.trim()
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
    count.textContent = `${selectedTaskIds.size} task${selectedTaskIds.size === 1 ? "" : "s"} selected`;
    bar.style.display = selectedTaskIds.size ? "flex" : "none";
}

async function runBatch(action) {
    if (!selectedTaskIds.size) {
        alert("Please select at least one task.");
        return;
    }
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

function batchSetStatus(status) {
    if (status === "completed") runBatch("complete");
}

function batchSetDate(targetDate) {
    if (targetDate === "today") runBatch("shift_today");
}

function batchDelete() {
    if (confirm("Delete all selected tasks?")) runBatch("delete");
}

function applyFilters() {
    const query = (document.getElementById("search")?.value || "").toLowerCase().trim();

    document.querySelectorAll(".task-section").forEach(section => {
        const matchesFilter = currentFilter === "all" || section.dataset.section === currentFilter;
        section.style.display = matchesFilter ? "" : "none";
        if (!matchesFilter) return;

        section.querySelectorAll(".task-card").forEach(card => {
            const text = card.textContent.toLowerCase();
            const matchesSearch = !query || text.includes(query);
            card.style.display = matchesSearch ? "" : "none";
        });
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const today = new Date();
    const date = today.toLocaleDateString("en-US", {
        weekday: "long", year: "numeric", month: "long", day: "numeric"
    });
    const dateEl = document.getElementById("current-date");
    if (dateEl) dateEl.textContent = date;

    document.querySelectorAll(".filter-btn, .sidebar-filter-link").forEach(button => {
        button.addEventListener("click", event => {
            event.preventDefault();
            currentFilter = button.dataset.filter || "all";
            document.querySelectorAll(".filter-btn, .sidebar-filter-link").forEach(b => {
                b.classList.toggle("active", b.dataset.filter === currentFilter);
            });
            applyFilters();
        });
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
            priority: Number(form.get("priority") || 2)
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
});


