/* Smart Planner Calendar — task-aware, accessible month/week/day calendar. */
let currentView = "month";
let currentDate = new Date();

if (Number.isFinite(Number(window.currentYear)) &&
    Number.isFinite(Number(window.currentMonth)) &&
    Number(window.currentYear) > 0 &&
    Number(window.currentMonth) >= 1 &&
    Number(window.currentMonth) <= 12) {
    currentDate = new Date(Number(window.currentYear), Number(window.currentMonth) - 1, 1);
}

document.addEventListener("DOMContentLoaded", () => {
    updateCalendarView();

    document.querySelectorAll(".view-btn").forEach(button => {
        button.addEventListener("click", () => {
            document.querySelectorAll(".view-btn").forEach(item => item.classList.remove("active"));
            button.classList.add("active");
            currentView = button.dataset.view || "month";
            updateCalendarView();
        });
    });

    document.getElementById("prevMonthBtn")?.addEventListener("click", () => navigate(-1));
    document.getElementById("nextMonthBtn")?.addEventListener("click", () => navigate(1));
    document.getElementById("todayBtn")?.addEventListener("click", () => {
        currentDate = new Date();
        updateCalendarView();
    });
    document.getElementById("gcalSyncBtn")?.addEventListener("click", syncGoogleCalendar);

    document.getElementById("addTaskForm")?.addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        const taskId = String(data.get("id") || "").trim();
        const payload = {
            title: String(data.get("title") || "").trim(),
            subject: String(data.get("subject") || "").trim(),
            due_date: String(data.get("due_date") || ""),
            due_time: String(data.get("due_time") || "23:59"),
            estimated_minutes: Number(data.get("estimated_minutes") || 30),
            priority: normalizePriority(data.get("priority"))
        };

        if (!payload.title) {
            showCalendarMessage("Enter a task title before saving.", true);
            return;
        }

        const submit = form.querySelector('[type="submit"]');
        if (submit) submit.disabled = true;
        try {
            await apiRequest(taskId ? `/planner/update/${encodeURIComponent(taskId)}` : "/planner/add",
                "POST", payload);
            closeModal();
            window.location.reload();
        } catch (error) {
            showCalendarMessage(error.message || "Unable to save this task.", true);
        } finally {
            if (submit) submit.disabled = false;
        }
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            closeModal();
            closeCalendarModal();
        }
    });
});

function navigate(direction) {
    if (currentView === "month") currentDate.setMonth(currentDate.getMonth() + direction);
    else if (currentView === "week") currentDate.setDate(currentDate.getDate() + 7 * direction);
    else currentDate.setDate(currentDate.getDate() + direction);
    updateCalendarView();
}

function formatDateISO(value) {
    if (!value) return "";
    if (value instanceof Date) {
        return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
    }
    return String(value).trim().split("T")[0].split(" ")[0];
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
}

function normalizePriority(value) {
    if (typeof value === "number" || /^\d+$/.test(String(value ?? ""))) {
        const number = Number(value);
        return number === 1 ? 1 : number === 3 ? 3 : 2;
    }
    const normalized = String(value || "").toLowerCase();
    return normalized === "high" ? 1 : normalized === "low" ? 3 : 2;
}

function priorityName(value) {
    return ({ 1: "High", 2: "Medium", 3: "Low" })[normalizePriority(value)];
}

function getCalendarTasks() {
    return typeof rawTasksData !== "undefined" && Array.isArray(rawTasksData) ? rawTasksData : [];
}

function getCalendarToday() {
    return typeof serverToday !== "undefined" && serverToday ? serverToday : new Date();
}

function updateCalendarView() {
    const grid = document.getElementById("calendarGrid");
    const display = document.getElementById("currentMonthYearDisplay");
    if (!grid) return;

    grid.replaceChildren();
    grid.className = `calendar-grid view-${currentView}`;

    const weekdays = document.querySelector(".calendar-weekdays");
    if (weekdays) weekdays.style.display = currentView === "day" ? "none" : "grid";

    if (currentView === "month") renderMonthView(grid, display);
    else if (currentView === "week") renderWeekView(grid, display);
    else renderDayView(grid, display);
}

function renderMonthView(grid, display) {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    if (display) display.textContent = currentDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    for (let i = 0; i < firstDay; i++) {
        const blank = document.createElement("div");
        blank.className = "calendar-day empty";
        blank.setAttribute("aria-hidden", "true");
        grid.appendChild(blank);
    }
    for (let day = 1; day <= daysInMonth; day++) {
        grid.appendChild(createDayCell(new Date(year, month, day), false));
    }
}

function renderWeekView(grid, display) {
    const start = new Date(currentDate);
    start.setDate(start.getDate() - start.getDay());
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    if (display) {
        display.textContent = `${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
    }
    for (let offset = 0; offset < 7; offset++) {
        const date = new Date(start);
        date.setDate(start.getDate() + offset);
        grid.appendChild(createDayCell(date, true));
    }
}

function renderDayView(grid, display) {
    if (display) display.textContent = currentDate.toLocaleDateString(undefined, {
        weekday: "long", month: "long", day: "numeric", year: "numeric"
    });
    grid.appendChild(createDayCell(new Date(currentDate), true));
}

function createDayCell(date, showWeekday) {
    const dateISO = formatDateISO(date);
    const cell = document.createElement("section");
    cell.className = "calendar-day";
    cell.dataset.date = dateISO;
    cell.setAttribute("aria-label", date.toLocaleDateString(undefined, {
        weekday: "long", month: "long", day: "numeric", year: "numeric"
    }));

    if (dateISO === formatDateISO(getCalendarToday())) cell.classList.add("today");

    cell.addEventListener("dragover", event => {
        if (event.dataTransfer?.types.includes("text/plain")) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            cell.classList.add("drag-over");
        }
    });
    cell.addEventListener("dragleave", event => {
        if (!cell.contains(event.relatedTarget)) cell.classList.remove("drag-over");
    });
    cell.addEventListener("drop", async event => {
        event.preventDefault();
        cell.classList.remove("drag-over");
        const taskId = event.dataTransfer?.getData("text/plain");
        if (taskId) await updateTaskDeadline(taskId, dateISO);
    });
    cell.addEventListener("dblclick", () => openModalForDate(dateISO));

    const header = document.createElement("div");
    header.className = "day-header";
    const dayLabel = document.createElement("span");
    dayLabel.className = "day-number";
    dayLabel.textContent = showWeekday
        ? `${date.toLocaleDateString(undefined, { weekday: "short" })} ${date.getDate()}`
        : String(date.getDate());
    header.appendChild(dayLabel);

    const matchingTasks = getCalendarTasks()
        .filter(task => formatDateISO(task.due_date) === dateISO);
    const pendingMinutes = matchingTasks
        .filter(task => !task.completed)
        .reduce((total, task) => total + (Number(task.estimated_minutes) || 0), 0);

    if (pendingMinutes > 0) {
        const load = document.createElement("span");
        load.className = `workload-tag ${pendingMinutes > 120 ? "heavy" : pendingMinutes > 60 ? "moderate" : "light"}`;
        load.textContent = `${pendingMinutes}m`;
        load.title = `${pendingMinutes} minutes of pending work`;
        header.appendChild(load);
    }
    cell.appendChild(header);

    const tasks = document.createElement("div");
    tasks.className = "day-tasks";
    matchingTasks.forEach(task => {
        const badge = document.createElement("button");
        badge.type = "button";
        badge.draggable = true;
        const priority = normalizePriority(task.priority);
        badge.className = `calendar-task-badge priority-${priorityName(priority).toLowerCase()}`;
        if (task.completed) badge.classList.add("completed");
        badge.dataset.taskId = String(task.id);
        badge.title = `${task.title} — ${task.completed ? "Completed" : "Pending"}. Click to edit; drag to reschedule.`;
        badge.innerHTML = `<span class="badge-title">${escapeHTML(task.title)}</span><span class="badge-time">${Number(task.estimated_minutes) || 30}m</span>`;
        badge.addEventListener("dragstart", event => {
            event.dataTransfer.setData("text/plain", String(task.id));
            event.dataTransfer.effectAllowed = "move";
            badge.classList.add("is-dragging");
        });
        badge.addEventListener("dragend", () => badge.classList.remove("is-dragging"));
        badge.addEventListener("click", () => openCalendarTaskModal(task));
        tasks.appendChild(badge);
    });
    cell.appendChild(tasks);
    return cell;
}

async function updateTaskDeadline(taskId, newDateISO) {
    const task = getCalendarTasks()
        .find(item => String(item.id) === String(taskId));
    if (!task) {
        showCalendarMessage("That task could not be found. Refresh the calendar and try again.", true);
        return;
    }
    if (formatDateISO(task.due_date) === newDateISO) return;

    try {
        await apiRequest(`/planner/update/${encodeURIComponent(taskId)}`, "POST", {
            title: String(task.title || "").trim(),
            subject: task.subject || "",
            due_date: newDateISO,
            due_time: task.due_time || "23:59",
            estimated_minutes: Number(task.estimated_minutes) || 30,
            priority: normalizePriority(task.priority)
        });
        window.location.reload();
    } catch (error) {
        showCalendarMessage(error.message || "Could not move this task.", true);
    }
}

function openModalForDate(dateISO) {
    if (typeof window.openModal !== "function") {
        showCalendarMessage("The task editor is unavailable on this page.", true);
        return;
    }
    window.openModal();
    const dueDate = document.getElementById("modalDueDate");
    if (dueDate) dueDate.value = dateISO;
}

function openCalendarTaskModal(task) {
    const modal = document.getElementById("calendarTaskModal");
    if (!modal) {
        editTaskFromCalendar(task);
        return;
    }
    document.getElementById("calModalTitle").textContent = task.title || "Untitled task";
    document.getElementById("calModalSubject").textContent = task.subject || "General";
    document.getElementById("calModalDueDate").textContent = formatDateISO(task.due_date) || "Not scheduled";
    document.getElementById("calModalPriority").textContent = priorityName(task.priority);
    document.getElementById("calModalEst").textContent = String(Number(task.estimated_minutes) || 30);
    document.getElementById("calModalStatus").textContent = task.completed ? "Completed" : "Pending";

    const edit = document.getElementById("calEditBtn");
    const toggle = document.getElementById("calToggleBtn");
    const remove = document.getElementById("calDeleteBtn");
    if (edit) edit.onclick = () => { closeCalendarModal(); editTaskFromCalendar(task); };
    if (toggle) toggle.onclick = async () => {
        try {
            await apiRequest(`/planner/toggle/${encodeURIComponent(task.id)}`, "POST");
            window.location.reload();
        } catch (error) { showCalendarMessage(error.message, true); }
    };
    if (remove) remove.onclick = async () => {
        if (!window.confirm("Delete this task?")) return;
        try {
            await apiRequest(`/planner/delete/${encodeURIComponent(task.id)}`, "DELETE");
            window.location.reload();
        } catch (error) { showCalendarMessage(error.message, true); }
    };
    modal.classList.add("active", "open");
    modal.style.display = "flex";
}

function editTaskFromCalendar(task) {
    if (typeof window.editTask !== "function") {
        showCalendarMessage("The task editor is unavailable on this page.", true);
        return;
    }
    window.editTask(
        task.id, task.title || "", task.subject || "",
        Number(task.estimated_minutes) || 30,
        normalizePriority(task.priority),
        formatDateISO(task.due_date),
        task.due_time || "23:59"
    );
}

function closeCalendarModal() {
    const modal = document.getElementById("calendarTaskModal");
    if (modal) {
        modal.classList.remove("active", "open");
        modal.style.display = "none";
    }
}

function showCalendarMessage(message, isError = false) {
    let region = document.getElementById("calendar-message");
    if (!region) {
        region = document.createElement("div");
        region.id = "calendar-message";
        region.className = "calendar-message";
        region.setAttribute("role", "status");
        region.setAttribute("aria-live", "polite");
        document.querySelector(".calendar-page-wrapper")?.prepend(region);
    }
    region.textContent = message;
    region.classList.toggle("is-error", Boolean(isError));
}

async function syncGoogleCalendar() {
    const button = document.getElementById("gcalSyncBtn");
    if (!button) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Syncing…";
    try {
        const response = await apiRequest("/api/calendar/google/sync", "POST");
        button.textContent = "Synced";
        window.alert(`Google Calendar synced successfully.\n\nImported: ${response.pulled ?? 0}\nExported: ${response.pushed ?? 0}`);
        window.location.reload();
    } catch (error) {
        button.textContent = originalText;
        showCalendarMessage(error.message || "Google Calendar sync failed.", true);
    } finally {
        button.disabled = false;
        if (button.textContent === "Synced") {
            window.setTimeout(() => { button.textContent = originalText; }, 2000);
        }
    }
}
