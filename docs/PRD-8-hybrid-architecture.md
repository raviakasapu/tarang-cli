# PRD: Hybrid WebSocket Architecture for Tarang

## Overview

Implement a Claude Code-style hybrid architecture where:
- **Backend**: Runs agents with reasoning/planning (protected IP)
- **CLI**: Executes tools locally via WebSocket (filesystem access)

This enables dynamic codebase exploration while protecting proprietary orchestration logic.

---

## Philosophy: Long-Running Agentic Jobs

Tarang is not a simple request-response system. It's an **agentic orchestration platform** that:

1. **Decomposes complex tasks** into phases and milestones
2. **Runs long-running jobs** (5-30 minutes for complex features)
3. **Tracks progress** through structured milestones
4. **Persists state** for resumability after disconnection
5. **Uses agent hierarchy** (Orchestrator → Architect → Workers)

### Agent Hierarchy

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator (Manager Agent)                            │
│  ├── Classifies request (query vs build)                │
│  ├── Generates PRD with requirements                    │
│  ├── Creates milestones                                 │
│  └── Delegates to Architect                             │
├─────────────────────────────────────────────────────────┤
│  Architect (Manager Agent)                               │
│  ├── Receives milestones from Orchestrator              │
│  ├── Decomposes into phases                             │
│  ├── Phase 1: Delegate to Explorer (analysis)           │
│  ├── Phase 2+: Delegate to Coder (implementation)       │
│  └── Tracks phase completion                            │
├─────────────────────────────────────────────────────────┤
│  Explorer (Worker Agent)                                 │
│  ├── Read-only tools (list, read, search)               │
│  ├── Analyzes codebase structure                        │
│  └── Returns findings to Architect                      │
├─────────────────────────────────────────────────────────┤
│  Coder (Worker Agent)                                    │
│  ├── Full tools (read, write, edit, shell)              │
│  ├── Implements changes                                 │
│  ├── Validates with build/test                          │
│  └── Returns results to Architect                       │
└─────────────────────────────────────────────────────────┘
```

### Job Lifecycle

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ CREATED  │───►│ RUNNING  │───►│ PAUSED   │───►│ COMPLETED│
└──────────┘    └────┬─────┘    └────┬─────┘    └──────────┘
                     │               │
                     │   disconnect  │  resume
                     └───────────────┘
                           │
                     ┌─────▼─────┐
                     │  FAILED   │
                     └───────────┘
```

### Example: Complex Task Flow

```
User: "Build user authentication with login and signup"

┌─────────────────────────────────────────────────────────┐
│ Job: job_abc123                                          │
│ Status: RUNNING                                          │
│ Progress: Phase 2 of 4 (50%)                            │
├─────────────────────────────────────────────────────────┤
│                                                          │
│ Phase 1: Explore ✅ COMPLETED                            │
│   ├── Milestone: Analyze tech stack ✓                   │
│   ├── Milestone: Find existing auth ✓                   │
│   └── Milestone: Identify patterns ✓                    │
│                                                          │
│ Phase 2: Plan ⏳ IN PROGRESS                             │
│   ├── Milestone: Design auth flow ✓                     │
│   ├── Milestone: Plan file structure ⏳                 │
│   └── Milestone: Define API routes ○                    │
│                                                          │
│ Phase 3: Implement ○ PENDING                             │
│   ├── Milestone: Create User model ○                    │
│   ├── Milestone: Create auth routes ○                   │
│   ├── Milestone: Create login UI ○                      │
│   └── Milestone: Create signup UI ○                     │
│                                                          │
│ Phase 4: Validate ○ PENDING                              │
│   ├── Milestone: Run tests ○                            │
│   └── Milestone: Build succeeds ○                       │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### State Persistence (Supabase)

```python
# Job state stored in Supabase for resume capability
{
  "job_id": "job_abc123",
  "user_id": "user_456",
  "instruction": "Build user authentication with login and signup",
  "status": "running",  # created, running, paused, completed, failed

  # Progress tracking
  "current_phase": 2,
  "total_phases": 4,
  "current_milestone": "Plan file structure",
  "progress_percent": 35,

  # Phases with milestones
  "phases": [
    {
      "name": "Explore",
      "status": "completed",
      "milestones": [
        {"name": "Analyze tech stack", "status": "completed"},
        {"name": "Find existing auth", "status": "completed"},
        {"name": "Identify patterns", "status": "completed"}
      ],
      "result_summary": "React + TypeScript frontend, Express backend, no existing auth"
    },
    {
      "name": "Plan",
      "status": "in_progress",
      "milestones": [
        {"name": "Design auth flow", "status": "completed"},
        {"name": "Plan file structure", "status": "in_progress"},
        {"name": "Define API routes", "status": "pending"}
      ]
    }
  ],

  # Work completed (for resume)
  "files_created": [],
  "files_modified": [],
  "context_summary": "React 18 + TypeScript, Vite, Express backend on port 3001...",

  # WebSocket session
  "ws_session_id": "ws_xyz789",
  "last_heartbeat": "2024-01-15T10:15:00Z",

  # Timestamps
  "started_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-01-15T10:15:00Z",
  "completed_at": null
}
```

---

## Goals

1. **Direct filesystem access** - Agents can read/write files without upfront context guessing
2. **Protect IP** - System prompts, agent configs, orchestration logic stay on backend
3. **Real-time streaming** - Show tool calls and results as they happen
4. **Bi-directional communication** - Backend requests tools, CLI executes and returns

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI (Local Machine)                                             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  WebSocket Client                                            ││
│  │  ├── Receives tool requests from backend                    ││
│  │  ├── Executes tools locally (filesystem, shell)             ││
│  │  └── Streams results back to backend                        ││
│  ├─────────────────────────────────────────────────────────────┤│
│  │  Tool Executor                                               ││
│  │  ├── read_file(path) → content                              ││
│  │  ├── write_file(path, content) → result                     ││
│  │  ├── list_files(path, pattern) → files[]                    ││
│  │  ├── search_files(pattern) → matches[]                      ││
│  │  ├── edit_file(path, old, new) → result                     ││
│  │  └── shell(command) → output                                ││
│  ├─────────────────────────────────────────────────────────────┤│
│  │  UI Layer                                                    ││
│  │  ├── Show tool calls in real-time                           ││
│  │  ├── Show agent thinking/planning                           ││
│  │  └── Approval prompts for destructive operations            ││
│  └─────────────────────────────────────────────────────────────┘│
└────────────────────────┬────────────────────────────────────────┘
                         │ WebSocket (wss://)
                         │ Bidirectional streaming
┌────────────────────────▼────────────────────────────────────────┐
│  Backend (Railway)                                               │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  WebSocket Server                                            ││
│  │  ├── Manages client connections                             ││
│  │  ├── Routes tool requests to CLI                            ││
│  │  └── Receives tool results, forwards to agent               ││
│  ├─────────────────────────────────────────────────────────────┤│
│  │  Agent Framework                                             ││
│  │  ├── Orchestrator agent (PRD, task decomposition)           ││
│  │  ├── Coder agent (implementation)                           ││
│  │  ├── Explorer agent (codebase analysis)                     ││
│  │  └── System prompts (PROTECTED IP)                          ││
│  ├─────────────────────────────────────────────────────────────┤│
│  │  Remote Tool Adapters                                        ││
│  │  ├── Wrap tool calls as WebSocket requests                  ││
│  │  ├── Wait for CLI response                                  ││
│  │  └── Return result to agent                                 ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## WebSocket Protocol

### Connection Flow

```
1. CLI connects: wss://backend/v2/ws/agent?token=<auth>
2. Backend accepts, sends: {"type": "connected", "session_id": "abc123"}
3. CLI sends instruction: {"type": "execute", "instruction": "...", "cwd": "/path/to/project"}
4. Backend starts agent, streams events...
```

### Message Types

#### CLI → Backend

```typescript
// Start execution
{
  "type": "execute",
  "instruction": "explain the codebase",
  "cwd": "/Users/user/myproject",
  "session_id": "optional-resume-id"
}

// Tool result (response to tool request)
{
  "type": "tool_result",
  "request_id": "req_123",
  "result": {
    "content": "file contents here...",
    "lines_read": 50,
    "total_lines": 100
  }
}

// Tool error
{
  "type": "tool_error",
  "request_id": "req_123",
  "error": "File not found: src/missing.py"
}

// User approval response
{
  "type": "approval",
  "request_id": "req_456",
  "approved": true
}

// Cancel execution
{
  "type": "cancel"
}
```

#### Backend → CLI

```typescript
// Connection confirmed
{
  "type": "connected",
  "session_id": "abc123"
}

// Agent thinking/status
{
  "type": "thinking",
  "message": "Analyzing project structure..."
}

// Tool request (CLI should execute and return result)
{
  "type": "tool_request",
  "request_id": "req_123",
  "tool": "read_file",
  "args": {
    "file_path": "src/main.py",
    "max_lines": 500
  }
}

// Approval request (for destructive operations)
{
  "type": "approval_request",
  "request_id": "req_456",
  "tool": "write_file",
  "args": {
    "file_path": "src/app.py",
    "content": "..."
  },
  "description": "Create new React component"
}

// Phase started
{
  "type": "phase_start",
  "phase": 2,
  "total_phases": 4,
  "name": "Implement",
  "milestones": ["Create User model", "Create auth routes", "Create login UI"]
}

// Milestone update
{
  "type": "milestone_update",
  "phase": 2,
  "milestone": "Create User model",
  "status": "completed"  // in_progress, completed, failed
}

// Progress update
{
  "type": "progress",
  "phase": 2,
  "total_phases": 4,
  "milestone": "Create auth routes",
  "percent": 45,
  "message": "Creating authentication routes..."
}

// Execution complete
{
  "type": "complete",
  "summary": "Created 3 files, modified 2 files",
  "files_changed": ["src/App.tsx", "src/index.ts"],
  "phases_completed": 4,
  "milestones_completed": 8
}

// Error
{
  "type": "error",
  "message": "Agent execution failed",
  "recoverable": true,
  "phase": 3,
  "milestone": "Create login UI"
}

// Job paused (connection lost, will resume)
{
  "type": "paused",
  "job_id": "job_abc123",
  "resume_command": "tarang resume job_abc123",
  "phase": 2,
  "milestone": "Create auth routes"
}
```

---

## Tools

### Read-Only Tools (no approval needed)

| Tool | Args | Returns |
|------|------|---------|
| `list_files` | path, pattern, recursive | files[], count |
| `read_file` | file_path, start_line, end_line | content, total_lines |
| `search_files` | pattern, path, file_pattern | matches[] |
| `get_file_info` | file_path | size, modified, type |

### Write Tools (approval required by default)

| Tool | Args | Returns | Approval |
|------|------|---------|----------|
| `write_file` | file_path, content | lines_written | Yes |
| `edit_file` | file_path, old_text, new_text | replacements | Yes |
| `delete_file` | file_path | deleted | Yes |
| `create_directory` | path | created | No |

### Shell Tools (approval required)

| Tool | Args | Returns | Approval |
|------|------|---------|----------|
| `shell` | command, cwd, timeout | output, exit_code | Configurable |

---

## Resume & Recovery

### Resume Flow

```
1. User runs: tarang resume job_abc123
2. CLI connects to WebSocket with job_id
3. Backend loads job state from Supabase
4. Backend resumes from last milestone
5. Continues execution...
```

### CLI Usage

```bash
# Just type your instructions - no commands needed!
tarang "build user authentication"
tarang "explain the codebase"
tarang "fix the login bug"

# Options
tarang "refactor the API" -y     # Auto-approve all changes
tarang "add tests" -v            # Verbose output
tarang -p /path/to/project       # Specify project directory

# Interactive mode
tarang                           # Start interactive session
```

### Automatic Pause on Disconnect

When WebSocket disconnects mid-execution:
1. Backend detects disconnect (missed heartbeats)
2. Backend pauses job, saves state to Supabase
3. Agent state serialized (current phase, milestone, context)
4. User can resume later with `tarang resume`

---

## Implementation Phases

### Phase 1: Core WebSocket Infrastructure ✅ DONE

**Backend:**
- [x] WebSocket endpoint `/v2/ws/agent`
- [x] Connection manager with session tracking
- [x] Message router (parse type, dispatch handler)
- [x] Tool request/response correlation (request_id tracking)
- [x] Heartbeat mechanism for connection health

**CLI:**
- [x] WebSocket client with reconnection logic
- [x] Message handler for different event types
- [x] Tool executor registry
- [x] Basic UI for streaming events

**Files to create/modify:**
```
tarang-backend/
├── app/api/ws_agent.py          # NEW: WebSocket endpoint
├── app/services/ws_manager.py   # NEW: Connection manager
└── app/services/job_manager.py  # NEW: Job state management

tarang-cli/
├── src/tarang/ws/__init__.py    # NEW
├── src/tarang/ws/client.py      # NEW: WebSocket client
├── src/tarang/ws/executor.py    # NEW: Tool executor
└── src/tarang/ws/handlers.py    # NEW: Message handlers
```

### Phase 2: Remote Tool Adapters ✅ DONE

**Backend:**
- [x] `RemoteReadFileTool` - sends request, waits for response
- [x] `RemoteWriteFileTool` - sends request with approval flow
- [x] `RemoteListFilesTool` - sends request, waits for response
- [x] `RemoteSearchFilesTool` - sends request, waits for response
- [x] `RemoteEditFileTool` - sends request with approval flow
- [x] `RemoteShellTool` - sends request with approval flow
- [x] `RemoteToolRegistry` for agent tool injection
- [x] `HybridExecutor` to orchestrate agent with remote tools

**CLI:**
- [x] Tool executor with all file/shell operations (`ws/executor.py`)
- [x] Tool name mapping to implementations
- [x] Approval requests with Rich UI (`ws/handlers.py`)

**Files:**
```
tarang-backend/
└── app/tools/remote_tools.py    # NEW: Remote tool adapters

tarang-cli/
├── src/tarang/tools/__init__.py # NEW
├── src/tarang/tools/file_tools.py    # COPY from VibeCoder
├── src/tarang/tools/shell_tools.py   # COPY from VibeCoder
└── src/tarang/tools/validation_tools.py  # COPY from VibeCoder
```

### Phase 3: Job & Milestone Management ✅ DONE

**Backend:**
- [x] Job model in Supabase (jobs table) - `00003_jobs.sql` migration
- [x] JobManager service for CRUD operations - `job_manager.py`
- [x] Phase/milestone tracking with status updates
- [x] Job state persistence on disconnect - `ws_manager.py` pauses jobs
- [x] Resume capability from saved state - `hybrid_executor.py` resume flow

**CLI:**
- [x] Show phase/milestone progress with visual progress bar
- [x] Auto-approve flag (`-y`) for unattended execution
- [ ] `tarang resume [job_id]` command (WS client supports it)
- [ ] `tarang status [job_id]` command
- [ ] `tarang cancel [job_id]` command

**Supabase Schema (implemented in `00003_jobs.sql`):**
```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,  -- job_xxxxxxxxxxxx format
  user_id UUID REFERENCES profiles(id),
  instruction TEXT NOT NULL,
  status TEXT DEFAULT 'created',
  current_phase INTEGER DEFAULT 0,
  total_phases INTEGER DEFAULT 0,
  current_milestone TEXT,
  progress_percent INTEGER DEFAULT 0,
  phases JSONB DEFAULT '[]',
  files_created TEXT[] DEFAULT '{}',
  files_modified TEXT[] DEFAULT '{}',
  context_summary TEXT,
  agent_state JSONB,
  ws_session_id TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE job_events (
  id UUID PRIMARY KEY,
  job_id TEXT REFERENCES jobs(id),
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata JSONB,
  timestamp TIMESTAMPTZ DEFAULT NOW()
);
```

### Phase 4: Agent Integration ✅ DONE

**Backend:**
- [x] Create `RemoteToolProvider` - wraps tools for WebSocket (`app/tools/tool_provider.py`)
- [x] Handle tool timeouts (30s default for read, 5min for approval)
- [x] Stream agent thinking/phase/milestone to CLI
- [x] Integrate HybridExecutor with actual LLM calls
- [x] Plan generation and code generation via LLM
- [ ] Full Orchestrator → Architect → Explorer/Coder flow (future enhancement)
- [ ] Update YAML agent configs to use remote tools (optional)

**CLI:**
- [x] Show tool calls in real-time with icons
- [x] Show thinking/planning messages
- [x] Show phase/milestone progress bar
- [x] Handle approval prompts (y/n)
- [x] Auto-approve flag (-y)

**Files Created:**
```
tarang-backend/
├── app/tools/tool_provider.py   # NEW: Tool provider for agents
├── app/services/hybrid_executor.py  # UPDATED: LLM integration
└── app/services/llm_client.py   # Uses for LLM calls

tarang-cli/
└── src/tarang/ws/handlers.py    # UPDATED: Tool call display
```

### Phase 5: UI Polish & Error Handling

**CLI:**
- [ ] Rich progress bar for phases/milestones
- [ ] Diff viewer for file changes
- [ ] Reconnection with progress resume
- [ ] Graceful cancellation (Ctrl+C)
- [ ] Offline detection and auto-pause

**Backend:**
- [ ] Handle client disconnection mid-execution
- [ ] Auto-pause job on disconnect
- [ ] Timeout handling for unresponsive clients
- [ ] Rate limiting per user

---

## File Changes Summary

### Backend (tarang-backend)

```
app/
├── api/
│   ├── __init__.py              # Add ws_agent router
│   └── ws_agent.py              # NEW: WebSocket endpoint
├── services/
│   └── ws_manager.py            # NEW: Connection manager
├── tools/
│   ├── remote_tools.py          # NEW: Remote tool adapters
│   └── tool_provider.py         # NEW: Tool provider for agents
└── configs/agents/
    ├── coder.yaml               # Update to use remote tools
    └── explorer.yaml            # Update to use remote tools
```

### CLI (tarang-cli)

```
src/tarang/
├── ws/
│   ├── __init__.py              # NEW
│   ├── client.py                # NEW: WebSocket client
│   ├── executor.py              # NEW: Tool executor
│   └── handlers.py              # NEW: Message handlers
├── tools/
│   ├── __init__.py              # NEW
│   ├── file_tools.py            # NEW: Copy from VibeCoder
│   ├── shell_tools.py           # NEW: Copy from VibeCoder
│   └── validation_tools.py      # NEW: Copy from VibeCoder
├── cli.py                       # Update to use WebSocket mode
└── ui.py                        # Update for streaming UI
```

---

## Success Criteria

1. **Functional:**
   - [ ] Agent can read any file in user's project
   - [ ] Agent can write files with user approval
   - [ ] Agent can run shell commands with user approval
   - [ ] Execution streams in real-time

2. **Performance:**
   - [ ] Tool round-trip < 100ms for local operations
   - [ ] WebSocket connection stable for 10+ minutes
   - [ ] Reconnection within 5 seconds on disconnect

3. **Security:**
   - [ ] System prompts never sent to CLI
   - [ ] Agent configs never sent to CLI
   - [ ] Only tool calls and results transmitted

4. **UX:**
   - [ ] User sees what agent is doing in real-time
   - [ ] User can approve/reject file changes
   - [ ] User can cancel at any time (Ctrl+C)

---

## Open Questions

1. **Approval UX:** Individual approval vs batch approval?
   - Recommendation: Start with individual, add "approve all" option

2. **Shell command safety:** Whitelist commands or approve all?
   - Recommendation: Approve all shell commands by default

3. **Large file handling:** Stream large files or chunk?
   - Recommendation: Chunk with start_line/end_line like VibeCoder

4. **Offline mode:** Support fully local execution?
   - Recommendation: Future enhancement, not MVP

---

## Timeline Estimate

| Phase | Effort |
|-------|--------|
| Phase 1: WebSocket Infrastructure | 1-2 days |
| Phase 2: Remote Tools | 1 day |
| Phase 3: Agent Integration | 1 day |
| Phase 4: UI Polish | 1 day |
| **Total** | **4-5 days** |

---

## Next Steps

1. Review and approve this PRD
2. Start with Phase 1: WebSocket infrastructure
3. Copy VibeCoder tools to CLI for local execution
4. Build remote tool adapters on backend
5. Integrate and test end-to-end
