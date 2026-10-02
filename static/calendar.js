document.addEventListener("DOMContentLoaded", () => {
    const calendarEl = document.getElementById("calendar");
    const modal = document.getElementById("eventModal");
    const form = document.getElementById("eventForm");
    const eventId = document.getElementById("eventId");
    const titleInput = document.getElementById("eventTitleInput");
    const startInput = document.getElementById("eventStartInput");
    const endInput = document.getElementById("eventEndInput");
    const allDayInput = document.getElementById("eventAllDayInput");
    const categoryInput = document.getElementById("eventCategoryInput");
    const locationInput = document.getElementById("eventLocationInput");
    const descriptionInput = document.getElementById("eventDescriptionInput");
    const deleteBtn = document.getElementById("deleteEventBtn");
    const modalTitle = document.getElementById("modalTitle");
    const externalEventsList = document.getElementById("external-events");
    const emptyQueueMsg = document.getElementById("emptyQueueMsg");
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

    if (!calendarEl || !form) return;

    const requestOptions = (method, body) => ({
        method,
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
            "X-Requested-With": "XMLHttpRequest"
        },
        body: body !== undefined ? JSON.stringify(body) : undefined
    });

    const toInputValue = value => {
        if (!value) return "";
        // Accept both "YYYY-MM-DD" (all-day) and full ISO datetimes.
        return value.length === 10 ? `${value}T00:00` : value.slice(0, 16);
    };

    function toast(message, isError = false) {
        const el = document.createElement("div");
        el.className = `calendar-toast${isError ? " error" : ""}`;
        el.textContent = message;
        document.body.appendChild(el);
        requestAnimationFrame(() => el.classList.add("show"));
        setTimeout(() => {
            el.classList.remove("show");
            setTimeout(() => el.remove(), 300);
        }, 3500);
    }

    // ---------------------------------------------------------------
    // FullCalendar setup
    // ---------------------------------------------------------------
    const calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: "dayGridMonth",
        height: "auto",
        selectable: true,
        editable: true,
        droppable: true,
        eventStartEditable: info => info.event.extendedProps.entity_type === "event",
        eventDurationEditable: info => info.event.extendedProps.entity_type === "event",
        events: async (info, success, failure) => {
            try {
                const response = await fetch(
                    `/api/calendar/feed?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`
                );
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "Unable to load calendar.");
                success((data.feed || []).map(item => ({
                    id: `${item.entity_type}:${item.id}`,
                    title: `${item.entity_type === "task" ? "⏰ Due: " : "📅 "}${item.title}`,
                    start: item.start_time,
                    end: item.end_time,
                    allDay: Boolean(item.is_all_day),
                    startEditable: item.entity_type === "event",
                    durationEditable: item.entity_type === "event",
                    extendedProps: item
                })));
            } catch (error) {
                console.error(error);
                failure(error);
            }
        },
        select: info => {
            openCreateModal(info.startStr, info.endStr, info.allDay);
        },
        eventClick: info => {
            const item = info.event.extendedProps;
            if (item.entity_type === "task") {
                window.location.href = "/planner";
                return;
            }
            openEditModal(item);
        },

        // Dragging a task card in from the sidebar queue.
        eventReceive: async info => {
            const taskId = info.event.extendedProps.taskId;
            const start = info.event.start;
            const end = info.event.end || new Date(start.getTime() + 30 * 60000);
            info.event.remove(); // remove FullCalendar's placeholder; the feed is the source of truth

            try {
                const response = await fetch("/api/calendar/events/add", requestOptions("POST", {
                    title: info.draggedEl?.dataset?.title || "Scheduled Task",
                    start_time: toLocalIso(start),
                    end_time: toLocalIso(end),
                    category: info.draggedEl?.dataset?.subject || "General",
                    description: "",
                    location: "",
                    is_all_day: false,
                    task_id: taskId
                }));
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "Unable to schedule task.");

                const cardEl = externalEventsList.querySelector(`[data-id="${taskId}"]`);
                if (cardEl) cardEl.remove();
                updateEmptyQueueState();

                calendar.refetchEvents();
                toast("Task scheduled on your calendar.");
            } catch (error) {
                toast(error.message, true);
            }
        },

        // Dragging an existing event to a new time.
        eventDrop: async info => {
            if (info.event.extendedProps.entity_type !== "event") { info.revert(); return; }
            const ok = await persistEventTimes(info.event);
            if (!ok) info.revert();
        },

        // Resizing an existing event's duration.
        eventResize: async info => {
            if (info.event.extendedProps.entity_type !== "event") { info.revert(); return; }
            const ok = await persistEventTimes(info.event);
            if (!ok) info.revert();
        }
    });

    function toLocalIso(date) {
        const pad = n => String(n).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    async function persistEventTimes(event) {
        const props = event.extendedProps;
        const rawId = String(event.id).split(":")[1];
        const start = event.start;
        const end = event.end || new Date(start.getTime() + 30 * 60000);
        try {
            const response = await fetch(`/api/calendar/events/update/${encodeURIComponent(rawId)}`, requestOptions("POST", {
                title: props.title,
                description: props.description || "",
                location: props.location || "",
                category: props.category || "General",
                start_time: event.allDay ? start.toISOString().slice(0, 10) : toLocalIso(start),
                end_time: event.allDay ? end.toISOString().slice(0, 10) : toLocalIso(end),
                is_all_day: event.allDay
            }));
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Unable to move event.");
            toast("Event updated.");
            return true;
        } catch (error) {
            toast(error.message, true);
            return false;
        }
    }

    function updateEmptyQueueState() {
        if (!emptyQueueMsg) return;
        const hasCards = externalEventsList.querySelector(".glass-draggable-task") !== null;
        emptyQueueMsg.style.display = hasCards ? "none" : "";
    }

    // Make Planner task cards in the sidebar draggable onto the calendar.
    if (externalEventsList) {
        new FullCalendar.Draggable(externalEventsList, {
            itemSelector: ".glass-draggable-task",
            eventData: el => ({
                title: el.dataset.title,
                duration: { minutes: parseInt(el.dataset.duration, 10) || 30 },
                extendedProps: { taskId: el.dataset.id }
            })
        });
    }

    // ---------------------------------------------------------------
    // Modal handling
    // ---------------------------------------------------------------
    function openCreateModal(start = "", end = "", isAllDay = false) {
        modalTitle.textContent = "Schedule Event";
        form.reset();
        eventId.value = "";
        titleInput.value = "";
        allDayInput.checked = Boolean(isAllDay);
        startInput.value = toInputValue(start);
        endInput.value = toInputValue(end || start);
        categoryInput.value = "";
        locationInput.value = "";
        descriptionInput.value = "";
        deleteBtn.style.display = "none";
        modal.style.display = "flex";
    }

    function openEditModal(item) {
        modalTitle.textContent = "Edit Event";
        eventId.value = item.id;
        titleInput.value = item.title || "";
        allDayInput.checked = Boolean(item.is_all_day);
        startInput.value = toInputValue(item.start_time);
        endInput.value = toInputValue(item.end_time);
        categoryInput.value = item.category || "";
        locationInput.value = item.location || "";
        descriptionInput.value = item.description || "";
        deleteBtn.style.display = "inline-flex";
        modal.style.display = "flex";
    }

    window.closeEventModal = () => {
        modal.style.display = "none";
        form.reset();
        eventId.value = "";
    };

    form.addEventListener("submit", async event => {
        event.preventDefault();
        const id = eventId.value;
        const isAllDay = allDayInput.checked;
        const payload = {
            title: titleInput.value.trim(),
            start_time: isAllDay ? startInput.value.slice(0, 10) : startInput.value,
            end_time: isAllDay ? endInput.value.slice(0, 10) : endInput.value,
            description: descriptionInput.value.trim(),
            location: locationInput.value.trim(),
            category: categoryInput.value.trim() || "General",
            is_all_day: isAllDay
        };

        const url = id
            ? `/api/calendar/events/update/${encodeURIComponent(id)}`
            : "/api/calendar/events/add";

        try {
            const response = await fetch(url, requestOptions("POST", payload));
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Unable to save event.");
            window.closeEventModal();
            calendar.refetchEvents();
        } catch (error) {
            toast(error.message, true);
        }
    });

    window.deleteCurrentEvent = async () => {
        const id = eventId.value;
        if (!id || !confirm("Delete this event?")) return;

        try {
            const response = await fetch(
                `/api/calendar/events/delete/${encodeURIComponent(id)}`,
                requestOptions("DELETE")
            );
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Unable to delete event.");
            window.closeEventModal();
            calendar.refetchEvents();
        } catch (error) {
            toast(error.message, true);
        }
    };

    calendar.render();
    updateEmptyQueueState();

    // ---------------------------------------------------------------
    // Google Calendar sync
    // ---------------------------------------------------------------
    const gcalStatusText = document.getElementById("gcalStatusText");
    const gcalActionBtn = document.getElementById("gcalActionBtn");
    let gcalState = { configured: false, connected: false };

    async function refreshGcalStatus() {
        try {
            const response = await fetch("/api/calendar/google/status");
            const data = await response.json();
            gcalState = data;
        } catch (error) {
            gcalState = { configured: false, connected: false };
        }
        renderGcalButton();
    }

    function renderGcalButton() {
        if (!gcalStatusText || !gcalActionBtn) return;
        if (!gcalState.configured) {
            gcalStatusText.textContent = "Google Calendar sync isn't set up on this server yet.";
            gcalActionBtn.textContent = "Not Available";
            gcalActionBtn.disabled = true;
            return;
        }
        gcalActionBtn.disabled = false;
        if (gcalState.connected) {
            gcalStatusText.textContent = gcalState.last_synced_at
                ? `Connected. Last synced ${new Date(gcalState.last_synced_at).toLocaleString()}.`
                : "Connected. Not synced yet.";
            gcalActionBtn.textContent = "Sync Now";
        } else {
            gcalStatusText.textContent = "Connect your Google account to sync events both ways.";
            gcalActionBtn.textContent = "Connect Google Calendar";
        }
    }

    window.handleGcalAction = async () => {
        if (!gcalState.configured) return;
        if (!gcalState.connected) {
            window.location.href = "/api/calendar/google/connect";
            return;
        }
        gcalActionBtn.disabled = true;
        gcalActionBtn.textContent = "Syncing…";
        try {
            const response = await fetch("/api/calendar/google/sync", requestOptions("POST"));
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Sync failed.");
            toast(data.message || "Synced with Google Calendar.");
            calendar.refetchEvents();
        } catch (error) {
            toast(error.message, true);
        } finally {
            await refreshGcalStatus();
        }
    };

    // Show a toast for the OAuth callback redirect, then clean the URL.
    const params = new URLSearchParams(window.location.search);
    if (params.has("gcal_connected")) {
        toast("Google Calendar connected.");
        window.history.replaceState({}, "", window.location.pathname);
    } else if (params.has("gcal_error")) {
        toast("Google Calendar connection failed. Please try again.", true);
        window.history.replaceState({}, "", window.location.pathname);
    }

    refreshGcalStatus();
});
