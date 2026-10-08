let currentView = "month";
let currentDate = new Date();

if (typeof currentYear !== "undefined" && typeof currentMonth !== "undefined" && currentYear && currentMonth) {
    currentDate = new Date(currentYear, currentMonth - 1, 1);
}

document.addEventListener("DOMContentLoaded", function () {
    updateCalendarView();

    document.querySelectorAll(".view-btn").forEach(btn => {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".view-btn").forEach(b => b.classList.remove("active"));
            this.classList.add("active");
            currentView = this.dataset.view;
            updateCalendarView();
        });
    });

    document.getElementById("prevMonthBtn")?.addEventListener("click", () => {
        if (currentView === "month") currentDate.setMonth(currentDate.getMonth() - 1);
        else if (currentView === "week") currentDate.setDate(currentDate.getDate() - 7);
        else if (currentView === "day") currentDate.setDate(currentDate.getDate() - 1);
        updateCalendarView();
    });

    document.getElementById("nextMonthBtn")?.addEventListener("click", () => {
        if (currentView === "month") currentDate.setMonth(currentDate.getMonth() + 1);
        else if (currentView === "week") currentDate.setDate(currentDate.getDate() + 7);
        else if (currentView === "day") currentDate.setDate(currentDate.getDate() + 1);
        updateCalendarView();
    });

    document.getElementById("todayBtn")?.addEventListener("click", () => {
        currentDate = new Date();
        updateCalendarView();
    });

    document.getElementById("gcalSyncBtn")?.addEventListener("click", syncGoogleCalendar);
});

function updateCalendarView() {
    const grid = document.getElementById("calendarGrid");
    const display = document.getElementById("currentMonthYearDisplay");
    if (!grid) return;

    grid.innerHTML = "";
    grid.className = `calendar-grid view-${currentView}`;

    if (currentView === "month") renderMonthView(grid, display);
    else if (currentView === "week") renderWeekView(grid, display);
    else if (currentView === "day") renderDayView(grid, display);
}

function formatDateISO(d) {
    if (!d) return "";
    if (typeof d === "string") {
        return d.split("T")[0].split(" ")[0];
    }
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

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

function renderDayView(grid, display) {
    const dateISO = formatDateISO(currentDate);
    if (display) {
        display.textContent = currentDate.toLocaleDateString('default', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    }
    const dayCell = createDayCell(currentDate.getDate(), dateISO, true);
    dayCell.classList.add("single-day-view");
    grid.appendChild(dayCell);
}

function createDayCell(dayNumber, dateISO, showWeekdayLabel = false) {
    const dayCell = document.createElement("div");
    dayCell.classList.add("calendar-day");
    dayCell.dataset.date = dateISO;
    if (dateISO === formatDateISO(serverToday)) dayCell.classList.add("today");

    // HTML5 Drag and Drop handlers
    dayCell.addEventListener("dragover", (e) => {
        e.preventDefault();
        dayCell.classList.add("drag-over");
    });

    dayCell.addEventListener("dragleave", () => {
        dayCell.classList.remove("drag-over");
    });

    dayCell.addEventListener("drop", async (e) => {
        e.preventDefault();
        dayCell.classList.remove("drag-over");
        const taskId = e.dataTransfer.getData("text/plain");
        if (taskId) {
            await updateTaskDeadline(taskId, dateISO);
        }
    });

    let headerHTML = `<span class="day-number">${dayNumber}</span>`;
    if (showWeekdayLabel) {
        const d = new Date(dateISO + "T00:00:00");
        headerHTML = `<span class="weekday-label">${d.toLocaleDateString('default', { weekday: 'short' })}</span> ` + headerHTML;
    }

    // Normalized Task Matching across ISO string formats
    const matchingTasks = (typeof rawTasksData !== "undefined" ? rawTasksData : []).filter(t => {
        if (!t.due_date) return false;
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

    const tasksContainer = dayCell.querySelector(".day-tasks");

    matchingTasks.forEach(task => {
        const taskBadge = document.createElement("div");
        taskBadge.draggable = true;
        
        const priorityClass = (task.priority || 'medium').toLowerCase();
        taskBadge.className = `calendar-task-badge priority-${priorityClass}`;
        if (task.completed) taskBadge.classList.add("completed");

        taskBadge.innerHTML = `
            <span class="badge-title">${task.title}</span>
            <span class="badge-time">${task.estimated_minutes || 30}m</span>
        `;
        
        taskBadge.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("text/plain", task.id);
        });

        taskBadge.addEventListener("click", (e) => {
            e.stopPropagation();
            openCalendarTaskModal(task);
        });

        tasksContainer.appendChild(taskBadge);
    });

    dayCell.addEventListener("dblclick", () => {
        if (typeof openModal === "function") {
            const dueInput = document.getElementById("modalDueDate");
            if (dueInput) dueInput.value = dateISO;
            openModal();
        }
    });

    return dayCell;
}

// Deadline Update using strict YYYY-MM-DD pattern
async function updateTaskDeadline(taskId, newDateISO) {
    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        const response = await await apiRequest(`/planner/update/${taskId}`, "POST", {
    title: task.title,
    subject: task.subject,
    due_date: newDueDate,
    due_time: newDueTime,
    estimated_minutes: task.estimated_minutes || 30,
    priority: task.priority || 2
});

        if (response.ok) {
            window.location.reload();
        } else {
            alert("Failed to update deadline.");
        }
    } catch (err) {
        console.error("Error updating deadline:", err);
    }
}

function openCalendarTaskModal(task) {
    const modal = document.getElementById("calendarTaskModal");
    if (!modal) return;

    const formattedDate = formatDateISO(task.due_date);

    document.getElementById("calModalTitle").textContent = task.title;
    document.getElementById("calModalSubject").textContent = task.subject || "General";
    document.getElementById("calModalDueDate").textContent = formattedDate || "None";
    document.getElementById("calModalPriority").textContent = task.priority || "Medium";
    document.getElementById("calModalEst").textContent = task.estimated_minutes || 30;
    document.getElementById("calModalStatus").textContent = task.completed ? "Completed" : "Pending";

    const editBtn = document.getElementById("calEditBtn");
    const toggleBtn = document.getElementById("calToggleBtn");
    const deleteBtn = document.getElementById("calDeleteBtn");

    if (editBtn) {
        editBtn.onclick = () => {
            closeCalendarModal();
            if (typeof editTask === "function") {
                editTask(task.id, task.title, task.subject, task.estimated_minutes, task.priority, formattedDate);
            }
        };
    }

    if (toggleBtn) {
        toggleBtn.onclick = async () => {
            if (typeof toggleTask === "function") {
                await toggleTask(task.id);
                closeCalendarModal();
                window.location.reload();
            }
        };
    }

    if (deleteBtn) {
        deleteBtn.onclick = async () => {
            if (typeof deleteTask === "function") {
                await deleteTask(task.id);
                closeCalendarModal();
                window.location.reload();
            }
        };
    }

    modal.style.display = "flex";
}

function closeCalendarModal() {
    const modal = document.getElementById("calendarTaskModal");
    if (modal) modal.style.display = "none";
}

async function syncGoogleCalendar() {
    try {
        const response = await fetch('/calendar/sync-google', { method: 'POST' });
        const result = await response.json();
        if (result.success) {
            alert("Successfully synced with Google Calendar!");
            window.location.reload();
        } else if (result.auth_url) {
            window.location.href = result.auth_url;
        } else {
            alert(result.message || "Failed to sync with Google Calendar.");
        }
    } catch (err) {
        console.error("Google Calendar Sync Error:", err);
    }
}
