# Atlas AI PC Control 🤖🖥️

**Atlas AI PC Control** is a universal multi-agent AI system designed for complete automation and control of Windows PCs.

The system combines high-speed terminal execution (PowerShell/CMD) with neural computer vision (**OmniParser**) to interact directly with graphical user interfaces (GUI) across any desktop application.

---

## 🌟 Key Features

- 🚀 **Hybrid Control (Terminal-First & Vision GUI):**
  - Prioritizes high-speed execution via console commands (file management, system queries, process execution).
  - Automatically delegates to the **Window Agent** equipped with the **OmniParser Engine** whenever UI interaction (buttons, menus, input fields) is required.
- 👁️ **Neural Computer Vision (OmniParser):**
  - Scans active application windows on the fly, detects clickable elements, input fields, and text blocks, mapping them to coordinates and unique element IDs.
- ⌨️ **Bulletproof Keyboard & Mouse Emulation:**
  - Full Unicode and multi-language support (Cyrillic, CJK, emojis) using isolated clipboard injection (`Shift + Insert`).
  - Immune to system keyboard layout switching and Windows 11 UWP application constraints.
- 🔌 **Flexible REST API & Background Task Queue:**
  - Built-in **FastAPI** server supporting both synchronous HTTP requests and background asynchronous task execution.
- 🌐 **Extensible WebSocket Protocol:**
  - Bi-directional WebSocket client support for remote control by external AI companions or custom platforms (e.g. Data-Sama integration).
- 🎙️ **Local Voice Interface (GUI Mode):**
  - Autonomous speech recognition (**Vosk ASR**) and local speech synthesis (**Silero TTS / gTTS**) for real-time voice control.

---

## 🏗️ Architecture Overview

```
                               ┌───────────────────────────┐
                               │        User / Client      │
                               └─────────────┬─────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
         ┌───────────────────────────┐               ┌───────────────────────────┐
         │     REST API & WebSockets │               │    Local Voice GUI (main) │
         │   server.py / FastAPI     │               │  Vosk ASR + Silero TTS    │
         └─────────────┬─────────────┘               └─────────────┬─────────────┘
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             ▼
                               ┌───────────────────────────┐
                               │     Atlas Main Agent      │
                               │   (agent/prompts/main)    │
                               └─────────────┬─────────────┘
                                             │
                      ┌──────────────────────┴──────────────────────┐
                      ▼                                             ▼
       ┌───────────────────────────┐                 ┌───────────────────────────┐
       │     Terminal (PowerShell) │                 │     Window Agent (GUI)    │
       │   execute_bash_command    │                 │   (prompts/window_agent)  │
       └───────────────────────────┘                 └─────────────┬─────────────┘
                                                                   │
                                                                   ▼
                                                     ┌───────────────────────────┐
                                                     │    OmniParser Engine      │
                                                     │  YOLO Vision + EasyOCR    │
                                                     └───────────────────────────┘
```

---

## 🛠️ Installation & Setup

### 1. Prerequisites
- **OS:** Windows 10 / 11
- **Python:** 3.10 – 3.12
- **PyTorch:** CUDA-enabled version recommended for accelerated OmniParser inference.

### 2. Clone Repository & Install Dependencies
```bash
git clone https://github.com/Oatis123/AI-PC-Contol.git
cd AI-PC-Contol

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration (`.env`)
Create a `.env` file in the project root directory:
```env
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

### 4. Download OmniParser Model Weights
Download the required YOLO & Caption weights for visual UI parsing:
```bash
python scripts/download_omniparser_weights.py
```

---

## 🚀 Usage

### Option 1: Start API Server (`server.py`)
Run the REST API server for external program integration:
```bash
python server.py
```
The server will run at `http://127.0.0.1:5050`.

#### API Usage Examples:

* **Synchronous Task Execution (`async_mode: false`):**
  ```bash
  curl -X POST "http://127.0.0.1:5050/api/execute" \
       -H "Content-Type: application/json" \
       -d '{"prompt": "Open calculator and multiply 25 by 4"}'
  ```

* **Asynchronous Task Execution (`async_mode: true`):**
  ```bash
  curl -X POST "http://127.0.0.1:5050/api/execute" \
       -H "Content-Type: application/json" \
       -d '{"prompt": "Create folder Test on desktop", "async_mode": true}'
  ```

---

### Option 2: Local Voice Assistant (`main.py`)
Run the local voice-activated assistant:
```bash
python main.py
```

---

## 📂 Repository Structure

```
AI-PC-Contol/
├── agent/                         # Multi-agent core logic
│   ├── agent.py                   # Main agent and task router
│   ├── window_interaction_agent.py# Window interaction agent for UI
│   ├── models/                    # LLM bindings (OpenRouter / LangChain)
│   ├── prompts/                   # System prompts for main and window agents
│   ├── tools/                     # System control tools (pc_control_tools.py)
│   └── vision/                    # OmniParser engine (omniparser_engine.py)
├── gui/                           # Visual overlay components (overlay.py)
├── scripts/                       # Model weight download scripts
├── utils/                         # Helper utilities
├── datasama_client.py             # WebSocket client for external platform integration
├── main.py                        # Voice GUI app with Vosk ASR & Silero TTS
├── server.py                      # FastAPI server (REST API + WS)
├── requirements.txt               # Python package dependencies
└── README.md                      # Project documentation
```

---

## 📝 License
This project is licensed under the MIT License.

<br/>

<p align="center">
  &nbsp;&nbsp;/&#92;_/\&nbsp;&nbsp;<br/>
  &nbsp;( o.o )&nbsp;<br/>
  &nbsp;&nbsp;&gt;&nbsp;^&nbsp;&lt;&nbsp;&nbsp;<br/>
  <br/>
  <b>Made with ❤️</b>
</p>
