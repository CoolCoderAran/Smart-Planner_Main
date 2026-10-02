document.addEventListener("DOMContentLoaded", fetchAnalyticsData);

let trendChartInstance;
let subjectChartInstance;
let priorityChartInstance;

async function fetchAnalyticsData() {
    try {
        const response = await fetch("/api/analytics");
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || "Unable to load analytics.");

        const data = result.data;
        renderMetrics(data.summary);
        renderTrendsChart(data.trends);
        renderSubjectChart(data.subject_breakdown);
        renderPriorityChart(data.priority_distribution);
        renderAchievements(data.achievements || []);
    } catch (error) {
        console.error("Error fetching analytics:", error);
    }
}

function renderMetrics(summary) {
    document.getElementById("metric-total-tasks").textContent = summary.total_tasks;
    document.getElementById("metric-completion-rate").textContent = `${summary.completion_rate}%`;
    document.getElementById("metric-overdue").textContent = summary.overdue_tasks;
    document.getElementById("metric-streak").textContent = `🔥 ${summary.streak} Days`;
    document.getElementById("metric-time-comparison").textContent =
        `${summary.total_estimated_minutes}m / ${summary.total_actual_focus_minutes}m`;
}

function renderTrendsChart(trends) {
    const canvas = document.getElementById("trendChart");
    if (!canvas || typeof Chart === "undefined") return;
    trendChartInstance?.destroy();

    trendChartInstance = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
            labels: trends.dates,
            datasets: [
                { label: "Completed Tasks", data: trends.completed, tension: 0.3, fill: false },
                { label: "Focus Minutes", data: trends.focus_minutes, tension: 0.3, fill: false }
            ]
        },
        options: { responsive: true }
    });
}

function renderSubjectChart(subjectData) {
    const canvas = document.getElementById("subjectChart");
    if (!canvas || typeof Chart === "undefined") return;
    subjectChartInstance?.destroy();

    const labels = Object.keys(subjectData);
    subjectChartInstance = new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
            labels: labels.length ? labels : ["No Data"],
            datasets: [
                {
                    label: "Estimated (Min)",
                    data: labels.length ? labels.map(key => subjectData[key].est_min) : [0]
                },
                {
                    label: "Actual Focus (Min)",
                    data: labels.length ? labels.map(key => subjectData[key].actual_min) : [0]
                }
            ]
        },
        options: { responsive: true }
    });
}

function renderPriorityChart(priorityDist) {
    const canvas = document.getElementById("priorityChart");
    if (!canvas || typeof Chart === "undefined") return;
    priorityChartInstance?.destroy();

    priorityChartInstance = new Chart(canvas.getContext("2d"), {
        type: "doughnut",
        data: {
            labels: ["High", "Medium", "Low"],
            datasets: [{
                data: [priorityDist.high || 0, priorityDist.medium || 0, priorityDist.low || 0]
            }]
        },
        options: { responsive: true }
    });
}

function renderAchievements(items) {
    const container = document.getElementById("achievements-container");
    if (!container) return;
    container.replaceChildren();

    items.forEach(item => {
        const card = document.createElement("div");
        card.className = `achievement-card ${item.unlocked ? "unlocked" : ""}`;

        const icon = document.createElement("div");
        icon.className = "achievement-icon";
        icon.textContent = item.unlocked ? "🏆" : "🔒";

        const info = document.createElement("div");
        info.className = "achievement-info";

        const title = document.createElement("h4");
        title.textContent = item.name || "Achievement";
        const description = document.createElement("p");
        description.textContent = item.desc || "";

        info.append(title, description);
        card.append(icon, info);
        container.appendChild(card);
    });
}


