# Smart Planner 🎓

> An intelligent, full-stack student productivity suite featuring natural language task shortcuts, automated study scheduling via Terra AI, custom focus sessions, and bidirectional Google Calendar integration. Built for the Congressional App Challenge.

---

## 🌐 Live Application
Access the production application: [smart-planner-wmjd.onrender.com](http://smart-planner-wmjd.onrender.com)

---

## 📸 Overview

Managing academic deadlines, extra-curriculars, and study schedules can quickly overwhelm students. **Smart Planner** bridges this gap by combining fast task capturing with an AI assistant (**Terra AI**) that transforms multi-day assignment goals into structured, realistic study blocks.

---

## ✨ Key Features

- **⚡ Fast Task Entry & Shortcuts**: Enter tasks using natural language shortcuts (e.g., `p1 tomorrow` for high-priority tasks due the next day).
- **🤖 Terra AI Assistant**: Multi-turn conversational planning assistant powered by OpenAI (`gpt-6-luna`) that breaks down complex homework assignments and proposes schedule blocks.
- **⏱️ Focus Mode & Soundscapes**: Built-in Pomodoro, Deep Work, and Exam Crunch focus timers with persistent state restoration across browser reloads.
- **📅 Google Calendar Sync**: OAuth 2.0 integration to sync focus blocks and deadlines directly with Google Calendar.
- **🎮 Gamification & Achievements**: Dynamic achievement tracking for study streaks, session counts, and focus time milestones.
- **🔒 Cross-User Security**: Strict IDOR protection, session isolation, user-scoped local storage, and CSRF token validation on all state-modifying endpoints.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.12+, Flask, SQLite (PRAGMA foreign keys enabled)
- **Frontend**: Vanilla JavaScript (ES6+), Modern CSS (`hub.css`), Jinja2 Templates, Lucide Icons
- **APIs & Integrations**: OpenAI API, Google Calendar API (OAuth 2.0)
- **Deployment & Hosting**: Render

---

## 📁 Repository Structure

```text
Smart-Planner_Main/
├── backend/
│   ├── ai.py               # Terra AI route handlers & prompt logic
│   ├── app.py              # Flask app factory & core blueprint registration
│   ├── auth.py             # User authentication & session management
│   ├── db.py               # Canonical database schema & connection helper
│   ├── focus.py            # Focus mode session management
│   ├── planner.py          # Task CRUD & subtask recursive logic
│   └── validator.py        # Schedule payload validation
├── static/
│   ├── ai.js               # Terra AI frontend chat controller
│   ├── focus.js            # Focus session timer & audio controls
│   ├── hub.css             # Main stylesheet & theme definitions
│   └── planner.js          # Task manager frontend controller
├── templates/              # Jinja2 HTML layout templates
├── .env.example            # Environment setup template
├── .gitignore              # Repository exclusions
├── build.sh                # Production build pipeline
├── LICENSE                 # GNU AGPLv3 License
├── README.md               # Repository documentation
└── requirements.txt        # Python package dependencies
```

---

## 🚀 Quickstart Guide

### Prerequisites
- **Python**: `3.12` or higher installed
- **Git**: Installed on your system

### Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/CoolCoderAran/Smart-Planner_Main.git
   cd Smart-Planner_Main
   ```

2. **Set up a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` to include your Flask `SECRET_KEY`, `OPENAI_API_KEY`, and Google OAuth credentials.

5. **Initialize the canonical database:**
   ```bash
   flask init-db
   ```

6. **Run the development server:**
   ```bash
   python app.py
   ```
   Navigate to `http://127.0.0.1:5000` in your web browser.

---

## 🛡️ Security Features

- **CSRF Protection**: All state-changing requests (`POST`, `PUT`, `DELETE`) require valid CSRF headers.
- **Data Isolation**: Database operations enforce ownership checks on `user_id` to prevent horizontal privilege escalation (IDOR).
- **Frontend History Scoping**: `localStorage` keys are explicitly scoped per username (`terra_chat_history_<username>`) to avoid cross-user chat leaks on shared devices.

---

## 📜 License

Distributed under the **GNU AGPLv3 License**. See `LICENSE` for more information.

## 🔐 Demo / Guest Credentials

To explore the application without creating a new account, you can log in using the following test credentials:

| Parameter | Credential |
| :--- | :--- |
| **Username** | `guest_student` |
| **Password** | `SmartPlanner2026!` |

> **Note:** The guest account includes pre-populated sample tasks, focus sessions, and achievements to give judges, testers, and auditors an instant view of the application's full feature set.
