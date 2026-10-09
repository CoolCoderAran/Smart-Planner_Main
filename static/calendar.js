/* ==========================================================================
   SMART PLANNER CALENDAR ENGINE
   Full bidirectional integration with planner.py backend and planner.js
   ========================================================================== */

let currentView = "month"; // Options: "month" | "week" | "day"
let currentDate = new Date();

// Respect server-injected year and month if provided
if (typeof currentYear !== "undefined" && typeof currentMonth !== "undefined" && currentYear && currentMonth) {
    currentDate = new Date(Number(currentYear), Number(currentMonth) - 1, 1);
}

document.addEventListener("DOMContentLoaded", function () {
    updateCalendarView();

    // View Picker Buttons
    const viewBtns = document.querySelectorAll(".view-btn");
    viewBtns.forEach(btn => {
        btn.addEventListener("click", function () {
            viewBtns.forEach(b => b.classList.remove("active"));
            this.classList.add("active");
            currentView = this.dataset.view || "month";
            updateCalendarView();
        });
    });

    // Navigation Buttons
    document.getElementById("prevMonthBtn")?.addEventListener("click", () => navigate(-1));
    document.getElementById("nextMonthBtn")?.addEventListener("click", () => navigate(1));
    document.getElementById("todayBtn")?.addEventListener("click", () => {
        currentDate = new Date();
        updateCalendarView();
    });

    // Google Calendar Sync Button
    document.getElementById("gcalSyncBtn")?.addEventListener("click", syncGoogleCalendar);

    // Escape Key Handler for Modals
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            closeCalendarModal();
            if (typeof closeModal === "function") closeModal();
        }
    });
});

// Navigation helper across Month/Week/Day views
function navigate(direction) {
    if (currentView === "month") {
        currentDate.setMonth(currentDate.getMonth() + direction);
    } else if (currentView === "week") {
        currentDate.setDate(currentDate.getDate() + (7 * direction));
    } else if (currentView === "day") {
        currentDate.setDate(currentDate.getDate() + direction);
    }
    updateCalendarView();
}

// Safely format dates to YYYY-MM-DD
function formatDateISO(d) {
    if (!d) return "";
    if (typeof d === "string") return d.split("T")[0].split(" ")[0];
    if (d instanceof Date) {
        const year = d.getFullYear();
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }
    return "";
}

// Normalize numeric priorities (1=High, 2=Medium, 3=Low)
function normalizePriority(p) {
    const num = Number(p);
    if (num === 1) return { name: "High", class: "priority-high" };
    if (num === 3) return { name: "Low", class: "priority-low" };
    return { name: "Medium", class: "priority-medium" };
}

function getCalendarTasks() {
    return typeof rawTasksData !== "undefined" && Array.isArray(rawTasksData) ? rawTasksData : [];
}

function getCalendarToday() {
    return typeof serverToday !== "undefined" && serverToday ? serverToday : formatDateISO(new Date());
}

// Core View Switcher
function updateCalendarView() {
    const grid = document.getElementById("calendarGrid");
    const display = document.getElementById("currentMonthYearDisplay");
    if (!grid) return;

    grid.innerHTML = "";
    grid.className = `calendar-grid view-${currentView}`;

    const weekdays = document.querySelector(".calendar-weekdays");
    if (weekdays) weekdays.style.display = currentView === "day" ? "none" : "grid";

    if (currentView === "month") renderMonthView(grid, display);
    else if (currentView === "week") renderWeekView(grid, display);
    else if (currentView === "day") renderDayView(grid, display);
}

// Render Month View
function renderMonthView(grid, display) {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    const monthNames = ["January", "February", "March", "April", "May", "June", 
                        "July", "August", "September", "October", "November", "December"];
    
    if (display) display.textContent = `${monthNames[month]} ${year}`;

    const firstDayIndex = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    for (let i = 0; i < firstDayIndex; i++) {
        const emptyCell = document.createElement("div");
        emptyCell.classList.add("calendar-day", "empty");
        grid.appendChild(emptyCell);
    }

    for (let day = 1; day <= daysInMonth; day++) {
        const dayDate = new Date(year, month, day);
        grid.appendChild(createDayCell(day, formatDateISO(dayDate)));
    }
}

// Render Week View
function renderWeekView(grid, display) {
    const startOfWeek = new Date(currentDate);
    startOfWeek.setDate(currentDate.getDate() - currentDate.getDay());

    const endOfWeek = new Date(startOfWeek);
    endOfWeek.setDate(startOfWeek.getDate() + 6);

    if (display) {
        display.textContent = `${startOfWeek.toLocaleDateString('default', { month: 'short', day: 'numeric' })} - ${endOfWeek.toLocaleDateString('default', { month: 'short', day: 'numeric', year: 'numeric' })}`;
    }

    for (let i = 0; i < 7; i++) {
        const dayDate = new Date(startOfWeek);
        dayDate.setDate(startOfWeek.getDate() + i);
        grid.appendChild(createDayCell(dayDate.getDate(), formatDateISO(dayDate), true));
    }
}

// Render Day View
function renderDayView(grid, display) {
    const dateISO = formatDateISO(currentDate);
    if (display) {
        display.textContent = currentDate.toLocaleDateString('default', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    }

    const dayCell = createDayCell(currentDate.getDate(), dateISO, true);
    dayCell.classList.add("single-day-view");
    grid.appendChild(dayCell);
}

// Construct Individual Day Cells with Task Badges and Workload Tag
function createDayCell(dayNumber, dateISO, showWeekdayLabel = false) {
    const dayCell = document.createElement("div");
    dayCell.classList.add("calendar-day");
    dayCell.dataset.date = dateISO;
    const todayISO = getCalendarToday();
    if (dateISO === todayISO) dayCell.classList.add("today");

    let headerHTML = `<span class="day-number">${dayNumber}</span>`;
    if (showWeekdayLabel) {
        const d = new Date(dateISO + "T00:00:00");
        const weekday = d.toLocaleDateString('default', { weekday: 'short' });
        headerHTML = `<span class="weekday-label">${weekday}</span> ` + headerHTML;
    }

    const matchingTasks = getCalendarTasks().filter(t => {
        if (!t.due_date || t.is_deleted) return false;
        return formatDateISO(t.due_date) === dateISO;
    });

    const pendingTasks = matchingTasks.filter(t => !t.completed);
    const totalMinutes = pendingTasks.reduce((acc, t) => acc + (parseInt(t.estimated_minutes) || 0), 0);

    let workloadHTML = "";
    if (totalMinutes > 0) {
        const loadClass = totalMinutes > 120 ? "heavy" : (totalMinutes > 60 ? "moderate" : "light");
        workloadHTML = `<span class="workload-tag ${loadClass}">${totalMinutes}m</span>`;
    }

    dayCell.innerHTML = `
        <div class="day-header">
            ${headerHTML}
            ${workloadHTML}
        </div>
        <div class="day-tasks"></div>
    `;

    // Drag and Drop Event Listeners for Rescheduling Tasks
    dayCell.addEventListener("dragover", event => {
        event.preventDefault();
        dayCell.classList.add("drag-over");
    });
    dayCell.addEventListener("dragleave", event => {
        if (!dayCell.contains(event.relatedTarget)) dayCell.classList.remove("drag-over");
    });
    dayCell.addEventListener("drop", async event => {
        event.preventDefault();
        dayCell.classList.remove("drag-over");
        const taskId = event.dataTransfer?.getData("text/plain");
        if (taskId) await updateTaskDeadline(taskId, dateISO);
    });

    // Double-click cell to create new task with pre-filled due date
    dayCell.addEventListener("dblclick", () => {
        if (typeof openModal === "function") {
            openModal();
            const dueInput = document.getElementById("modalDueDate");
            if (dueInput) dueInput.value = dateISO;
        }
    });

    const tasksContainer = dayCell.querySelector(".day-tasks");

    matchingTasks.forEach(task => {
        const taskBadge = document.createElement("button");
        taskBadge.type = "button";
        taskBadge.draggable = true;
        const isOverdue = !task.completed && dateISO < todayISO;
        const prioInfo = normalizePriority(task.priority);

        taskBadge.className = `calendar-task-badge ${prioInfo.class}`;
        if (task.completed) taskBadge.classList.add("completed");
        if (isOverdue) taskBadge.classList.add("overdue");

        taskBadge.innerHTML = `
            <span class="badge-title">${isOverdue ? '⚠️ ' : ''}${escapeHTML(task.title)}</span>
            <span class="badge-time">${task.estimated_minutes || 30}m</span>
        `;

        taskBadge.addEventListener("dragstart", event => {
            event.dataTransfer.setData("text/plain", String(task.id));
            taskBadge.classList.add("is-dragging");
        });
        taskBadge.addEventListener("dragend", () => taskBadge.classList.remove("is-dragging"));

        taskBadge.addEventListener("click", (e) => {
            e.stopPropagation();
            openCalendarTaskModal(task);
        });

        tasksContainer.appendChild(taskBadge);
    });

    return dayCell;
}

// Drag & Drop Reschedule API Request
async function updateTaskDeadline(taskId, newDateISO) {
    const task = getCalendarTasks().find(t => String(t.id) === String(taskId));
    if (!task || formatDateISO(task.due_date) === newDateISO) return;

    try {
        await apiRequest(`/planner/update/${encodeURIComponent(taskId)}`, "POST", {
            title: task.title,
            subject: task.subject || "",
            due_date: newDateISO,
            due_time: task.due_time || "23:59",
            estimated_minutes: Number(task.estimated_minutes) || 30,
            priority: task.priority || 2
        });
        window.location.reload();
    } catch (error) {
        alert(error.message || "Failed to reschedule task.");
    }
}

// Task Inspection Modal Handler
function openCalendarTaskModal(task) {
    const modal = document.getElementById("calendarTaskModal");
    if (!modal) return;

    const prioInfo = normalizePriority(task.priority);

    document.getElementById("calModalTitle").textContent = task.title || "Untitled Task";
    document.getElementById("calModalSubject").textContent = task.subject || "General";
    document.getElementById("calModalDueDate").textContent = formatDateISO(task.due_date) || "None";
    document.getElementById("calModalPriority").textContent = `${prioInfo.name} (P${task.priority || 2})`;
    document.getElementById("calModalEst").textContent = task.estimated_minutes || 30;
    document.getElementById("calModalStatus").textContent = task.completed ? "Completed" : "Pending";

    const editBtn = document.getElementById("calEditBtn");
    const toggleBtn = document.getElementById("calToggleBtn");
    const deleteBtn = document.getElementById("calDeleteBtn");

    if (editBtn) {
        editBtn.onclick = () => {
            closeCalendarModal();
            if (typeof editTask === "function") {
                editTask(task.id, task.title, task.subject, task.estimated_minutes, task.priority, formatDateISO(task.due_date), task.due_time);
            }
        };
    }

    if (toggleBtn) {
        toggleBtn.onclick = async () => {
            try {
                await apiRequest(`/planner/toggle/${encodeURIComponent(task.id)}`, "POST");
                closeCalendarModal();
                window.location.reload();
            } catch (error) {
                alert(error.message);
            }
        };
    }

    if (deleteBtn) {
        deleteBtn.onclick = async () => {
            if (!confirm("Are you sure you want to delete this task?")) return;
            try {
                await apiRequest(`/planner/delete/${encodeURIComponent(task.id)}`, "DELETE");
                closeCalendarModal();
                window.location.reload();
            } catch (error) {
                alert(error.message);
            }
        };
    }

    modal.classList.add("active", "open");
    modal.style.display = "flex";
}

function closeCalendarModal() {
    const modal = document.getElementById("calendarTaskModal");
    if (modal) {
        modal.classList.remove("active", "open");
        modal.style.display = "none";
    }
}

function escapeHTML(str) {
    return String(str || "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
}

// Optional Google Calendar Sync API
async function syncGoogleCalendar() {
    const button = document.getElementById("gcalSyncBtn");
    if (!button) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Syncing…";
    try {
        const response = await apiRequest("/api/calendar/google/sync", "POST");
        alert(`Google Calendar synced!\nImported: ${response.pulled ?? 0}\nExported: ${response.pushed ?? 0}`);
        window.location.reload();
    } catch (error) {
        alert(error.message || "Google Calendar sync failed.");
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}
