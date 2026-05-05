<div align="center">

![Things3 MCP Logo](https://github.com/rossshannon/Things3-MCP/raw/main/docs/images/Things3-MCP-logo.png)

# Things 3 MCP Server

</div>

This [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) server lets you use Claude Desktop to interact with your task management data in [Things 3](https://culturedcode.com/things). You can ask Claude or your MCP client of choice to create tasks, analyze projects, help manage priorities, and more.

This MCP server leverages a combination of the [Things.py](https://github.com/thingsapi/things.py) library and [Things 3’s AppleScript support](https://culturedcode.com/things/support/articles/4562654/), enabling reading and writing to Things 3.

## Why Things MCP?

This MCP server unlocks the power of AI for your task management:

- **Natural Language Task Creation**: Ask Claude to create richly-detailed tasks and descriptions in natural language
- **Smart Task Analysis**: Let Claude explore your project lists and focus areas and provide insights into your work
- **GTD & Productivity Workflows**: Let Claude help you implement productivity and prioritisation systems
- **Seamless Integration**: Works directly with your existing Things 3 data

## Features

- Access to all major Things lists (Inbox, Today, Upcoming, Logbook, Someday, etc.)
- Project and Area management and assignment
- Tagging operations for tasks and projects
- Advanced search capabilities
- Recent items tracking
- Support for nested data (projects within areas, todos within projects)
- Checklist/Subtask support - Read and display existing checklist items from todos

## Data Safety: Things SQLite is Read-Only

This server **never writes to Things' SQLite database directly**. Doing so would bypass Things' sync engine and risk corruption or loss across iCloud-synced devices.

- All SQLite reads use `sqlite3.connect("file:…?mode=ro&immutable=1", uri=True)` — kernel-enforced read-only handle.
- Reads go through the [`things-py`](https://github.com/thingsapi/things.py) library, which has no write API by design.
- All mutations (create / update / complete / cancel / move) flow through AppleScript via `tell application "Things3"`, so Things 3 owns its database and handles sync.

## Installation

#### Prerequisites
* Python 3.12+
* Claude Desktop
* Things 3 for MacOS

#### Step 1: Install the package

**Option A: Install from PyPI in a virtual environment (recommended)**
```bash
# Create a virtual environment in your home directory
python3 -m venv ~/.venvs/things3-mcp-env
source ~/.venvs/things3-mcp-env/bin/activate

# Install the package
pip install Things3-MCP-server==2.0.6
```

**Option B: Install from source (for development/contributors)**
```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh
# Restart your terminal afterwards

# Clone and install the package with development dependencies
git clone https://github.com/rossshannon/Things3-MCP
cd Things3-MCP
uv venv
uv pip install -e ".[dev]"  # Install in development mode with extra dependencies
```

### Step 2: Configure Claude Desktop
Edit the Claude Desktop configuration file:
```bash
code ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Add the Things server to the mcpServers key in the configuration file:

**Option A: Using PyPI package in virtual environment**
```json
{
    "mcpServers": {
        "things": {
            "command": "~/.venvs/things3-mcp-env/bin/Things3-MCP-server"
        }
    }
}
```

**Option B: Using source installation (for development/contributors)**
```json
{
    "mcpServers": {
        "things": {
            "command": "uv",
            "args": [
                "--directory",
                "/ABSOLUTE/PATH/TO/PARENT/FOLDER/Things3-MCP",
                "run",
                "Things3-MCP-server"
            ]
        }
    }
}
```

### Step 3: Restart Claude Desktop
Restart the Claude Desktop app to enable the integration.

## Reliable AFK Reads with the Local Bridge

macOS privacy controls can attach database access to whichever executable touches the Things data: Claude Desktop, `node`, a virtualenv Python, a terminal shell, or `/usr/bin/osascript`. That is why you can see repeated prompts such as “node would like to access data from other apps”, even after approving access before. The binary asking for access has changed, so macOS treats it as a different privacy subject.

The bridge setup moves protected reads out of the MCP server:

```text
Claude / MCP client
  -> Things3-MCP server
     -> local bridge socket
        -> signed Things3 MCP Bridge.app
           -> Things SQLite database
           -> JSON cache
```

The important part is that Full Disk Access is granted once to a stable app bundle:

```text
~/Applications/Things3 MCP Bridge.app
```

The MCP server then talks to that bridge over a local Unix socket and falls back to the last good cache when live access is unavailable.

This bridge currently covers read operations and cache snapshots. Write operations such as `add_todo`, `add_project`, `update_todo`, and `update_project` still use the MCP server's existing AppleScript path. That means writes may still require macOS Automation permission for the process running the MCP server.

The same bridge pattern can support writes, but that is a separate implementation step: route write tools through the signed bridge and grant Automation once for `Things3 MCP Bridge.app` to control `Things3.app`. Until then, treat the bridge as the durable AFK read path, not as a complete replacement for all Things access.

### Do I Need an Apple Developer Account?

No, not for your own Mac. You can self-sign the bridge app with a local Code Signing certificate. This gives macOS a stable local code identity for Full Disk Access.

You only need Apple Developer ID signing and notarisation if you want to distribute a ready-made app bundle to other people. For source users building their own bridge locally, a self-signed Code Signing certificate is enough.

Ad-hoc signing (`--adhoc`) is available for development tests, but it is not recommended for the real Full Disk Access grant because each rebuild can look like a different app to macOS.

### Build, Sign, Install

From a source checkout:

```bash
uv sync --dev
scripts/build_bridge_app.sh
scripts/sign_bridge_app.sh --identity "Things3 MCP Local"
scripts/install_bridge_launchagent.sh
```

If the signing step says the identity does not exist, create it:

- Open **Keychain Access**.
- Choose **Keychain Access -> Certificate Assistant -> Create a Certificate...**.
- Name it `Things3 MCP Local`.
- Set **Identity Type** to **Self Signed Root**.
- Set **Certificate Type** to **Code Signing**.
- Create it in your login keychain.
- If `security find-identity -v -p codesigning` still does not list it, open the certificate, expand **Trust**, and set **Code Signing** to **Always Trust**.

Then run the signing step again:

```bash
scripts/sign_bridge_app.sh --identity "Things3 MCP Local"
```

### Grant Full Disk Access

macOS does not allow scripts to grant this permission for you. After installing the bridge:

- Open **System Settings -> Privacy & Security -> Full Disk Access**.
- Remove any older `Things3 MCP Bridge` entry.
- Add `~/Applications/Things3 MCP Bridge.app`.
- Toggle it on.
- Re-run `scripts/install_bridge_launchagent.sh` to restart the LaunchAgent with the newly granted permission.

### Verify the Bridge

Check that the app is installed, signed, and reachable:

```bash
uv run python scripts/check_bridge.py
```

Then ask the installed bridge to take a live snapshot and populate the cache:

```bash
uv run python scripts/check_bridge.py --snapshot
```

Success means:

- `bridge_running` is `true`
- `socket_reachable` is `true`
- `code_signature_is_adhoc` is `false`
- `authorization_status` says the live snapshot succeeded
- `cache.available` is `true`

The cache lives at:

```text
~/Library/Application Support/Things3-MCP/cache/latest.json
```

The bridge logs live at:

```text
~/Library/Logs/Things3-MCP/bridge.log
~/Library/Logs/Things3-MCP/bridge.err.log
```

### MCP Provider Modes

For reliable AFK reads, use bridge/cache mode and do not let the MCP process fall back to direct database access:

```json
{
    "mcpServers": {
        "things": {
            "command": "uv",
            "args": [
                "--directory",
                "/ABSOLUTE/PATH/TO/Things3-MCP",
                "run",
                "Things3-MCP-server"
            ],
            "env": {
                "THINGS3_MCP_PROVIDER": "auto",
                "THINGS3_MCP_ALLOW_DIRECT_FALLBACK": "0"
            }
        }
    }
}
```

Available provider modes:

- `THINGS3_MCP_PROVIDER=auto` tries the bridge, then the JSON cache.
- `THINGS3_MCP_PROVIDER=bridge` requires the local bridge.
- `THINGS3_MCP_PROVIDER=cache` reads only the last snapshot.
- `THINGS3_MCP_PROVIDER=direct` uses the legacy `things-py` database access path from the MCP process.
- `THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1` allows `auto` mode to use direct database access as a last resort. This is convenient for development, but it can reintroduce macOS access prompts for Python, Node, Claude, or your terminal.

### Troubleshooting Bridge Setup

If `scripts/sign_bridge_app.sh` cannot find `Things3 MCP Local`, create the local Code Signing certificate as described above. `security find-identity -v -p codesigning` should show at least one valid identity.

If `code_signature_is_adhoc` is `true`, rebuild or re-sign with:

```bash
scripts/sign_bridge_app.sh --identity "Things3 MCP Local"
scripts/install_bridge_launchagent.sh
```

If `--snapshot` times out or reports a Full Disk Access hint, remove and re-add `~/Applications/Things3 MCP Bridge.app` in Full Disk Access, then restart the LaunchAgent:

```bash
scripts/install_bridge_launchagent.sh
uv run python scripts/check_bridge.py --snapshot
```

If Full Disk Access is granted but live SQLite discovery still cannot determine the current `ThingsData-*` folder, configure it explicitly when installing the bridge LaunchAgent:

```bash
THINGS3_MCP_DATA_FOLDER=ThingsData-ABC123 scripts/install_bridge_launchagent.sh
uv run python scripts/check_bridge.py --snapshot
```

This is deliberately a folder name, not a glob. The bridge avoids enumerating the Things group container during normal reads because that protected directory access can hang under macOS TCC.

Do not treat granting Full Disk Access to `python`, `node`, Claude Desktop, or your terminal as the durable fix. That may work briefly, but it puts you back in TCC prompt roulette. For reads, the durable setup is: signed bridge app owns protected database access; MCP clients talk to the bridge/cache. For writes, the durable setup will be: signed bridge app owns Things Automation or URL-scheme writes; MCP clients request writes through the bridge.

### Sample Usage with Claude Desktop
* “What’s on my todo list today?”
* “Create a todo to prepare for each of my 1-on-1s next week”
* “Evaluate my todos scheduled for today using the Eisenhower matrix.”
* “Help me conduct a GTD-style weekly review using Things.”

#### Tips
* Create a Project in Claude with custom instructions that explains how you use Things and organize areas, projects, tags, etc. Tell Claude what information you want included when it creates a new task (e.g., asking it to include relevant details in the task description, whether to use emojis, etc.).
* Try combining this with another MCP server that gives Claude access to your calendar. This will let you ask Claude to block time on your calendar for specific tasks, create tasks that relate to upcoming calendar events (e.g., prep for a meeting), etc.


### Available Tools

#### List Views
- `get_inbox` - Get todos from Inbox
- `get_today` - Get todos due today
- `get_upcoming` - Get upcoming todos
- `get_anytime` - Get todos from Anytime list
- `get_someday` - Get todos from Someday list
- `get_logbook` - Get completed todos
- `get_trash` - Get trashed todos

#### Random Sampling (for LLM Enrichment)
- `get_random_inbox` - Get a random sample of todos from Inbox
- `get_random_anytime` - Get a random sample of items from Anytime list
- `get_random_todos` - Get a random sample of todos, optionally filtered by project

#### Basic Operations
- `get_todos` - Get todos, optionally filtered by project
- `get_projects` - Get all projects
- `get_areas` - Get all areas

#### Tag Operations
- `get_tags` - Get all tags
- `get_tagged_items` - Get items with a specific tag

#### Search Operations
- `search_todos` - Simple search by title/notes
- `search_advanced` - Advanced search with multiple filters

#### Time-based Operations
- `get_recent` - Get recently created items

#### Modification Operations
- `add_todo` - Create a new todo with full parameter support
- `add_project` - Create a new project with tags and todos
- `update_todo` - Update an existing todo
- `update_project` - Update an existing project
- `show_item` - Show a specific item or list in Things
- `search_items` - Search for items in Things

## Tool Parameters

### get_todos
- `project_uuid` (optional) - Filter todos by project

### get_projects / get_areas / get_tags
- `include_items` (optional, default: false) - Include contained items

### search_advanced
- `status` - Filter by status (incomplete/completed/canceled)
- `start_date` - Filter by start date (YYYY-MM-DD)
- `deadline` - Filter by deadline (YYYY-MM-DD)
- `tag` - Filter by tag
- `area` - Filter by area UUID
- `type` - Filter by item type (to-do/project/heading)

### get_recent
- `period` - Time period (e.g., '3d', '1w', '2m', '1y')
- `limit` - Maximum number of items to return

### Random Sampling Tools
- `get_random_inbox(count=5)` - Get random sample from Inbox
- `get_random_anytime(count=5)` - Get random sample from Anytime list
- `get_random_todos(project_uuid=None, count=5)` - Get random sample of todos, optionally from specific project

### add_todo
- `title` - Title of the todo
- `notes` (optional) - Notes for the todo (supports Markdown formatting including checkboxes like `- [ ] Task`)
- `when` (optional) - When to schedule the todo (today, tomorrow, evening, anytime, someday, or YYYY-MM-DD)
- `deadline` (optional) - Deadline for the todo (YYYY-MM-DD)
- `tags` (optional) - Tags to apply to the todo
- `list_title` (optional) - Title of project/area to add to (must exactly match existing name)
- `list_id` (optional) - ID of project/area to add to (takes priority over list_title if both provided)
- **Note**: While Things’ native checklist feature (i.e., subtasks) cannot be created via AppleScript, you and your LLMs can use Markdown checkboxes in the notes field to achieve similar functionality. ![Things3 - Subtasks - Markdown Checklist](docs/images/Things3-subtasks-markdown-checklist.png)

### update_todo
- `id` - ID of the todo to update
- `title` (optional) - New title
- `notes` (optional) - New notes
- `when` (optional) - When to schedule the todo (today, tomorrow, evening, anytime, someday, or YYYY-MM-DD)
- `deadline` (optional) - Deadline for the todo (YYYY-MM-DD)
- `tags` (optional) - New tags
- `completed` (optional) - Mark as completed
- `canceled` (optional) - Mark as canceled
- `list_name` (optional) - Name of built-in list, project, or area to move the todo to. For built-in lists use: "Inbox", "Today", "Anytime", "Someday". For projects/areas, use the exact name.
- `list_id` (optional) - ID of project/area to move the todo to (takes priority over list_name if both provided)

### add_project
- `title` - Title of the project
- `notes` (optional) - Notes for the project
- `when` (optional) - When to schedule the project
- `deadline` (optional) - Deadline for the project
- `tags` (optional) - Tags to apply to the project
- `area_title` or `area_id` (optional) - Title or ID of area to add to (must exactly match an existing area title — look them up with `get_areas`)
- `todos` (optional) - Initial todos to create in the project

### update_project
- `id` - ID of the project to update
- `title` (optional) - New title
- `notes` (optional) - New notes
- `when` (optional) - When to schedule the project (today, tomorrow, evening, anytime, someday, or YYYY-MM-DD)
- `deadline` (optional) - Deadline for the project (YYYY-MM-DD)
- `tags` (optional) - New tags
- `completed` (optional) - Mark as completed
- `canceled` (optional) - Mark as canceled

### show_item
- `id` - ID of item to show, or one of: inbox, today, upcoming, anytime, someday, logbook
- `query` (optional) - Optional query to filter by
- `filter_tags` (optional) - Optional tags to filter by

## Usage Examples

### Creating Todos with List Assignment

```python
# Create todo in Inbox (default)
add_todo(title="Review quarterly report")

# Create todo in a built-in list
add_todo(title="Call dentist", when="today")
add_todo(title="Plan vacation", when="someday")

# Create todo in a project by name
add_todo(title="Design new logo", list_title="Website Redesign")

# Create todo in a project by ID (more precise, recommended for automation)
add_todo(title="Write documentation", list_id="ABC123DEF456")

# When both are provided, list_id takes priority
add_todo(
    title="Important task",
    list_id="ABC123DEF456",     # This will be used
    list_title="Other Project"  # This will be ignored
)
```

### Moving Todos Between Lists

```python
# Move to built-in list
update_todo(id="TODO123", list_name="Today")
update_todo(id="TODO456", list_name="Someday")

# Move to project by name
update_todo(id="TODO789", list_name="Website Redesign")

# Move to project by ID (recommended for precision)
update_todo(id="TODO101", list_id="ABC123DEF456")
```

### When to Use ID vs Title

- **Use `list_title`/`list_name`** when:
  - Working interactively with human-readable names
  - You're certain the name is unique and won't change
  - Creating simple scripts or one-off tasks

- **Use `list_id`** when:
  - Building automation or applications
  - You need precision and reliability
  - Working with projects/areas that might have similar names

## Using Tags
Things will automatically create missing tags when they are added to a task or project. Configure your LLM to do a lookup of your tags first before making changes if you want to control this.

## LLM Enrichment Workflows

The random sampling tools (`get_random_inbox`, `get_random_anytime`, `get_random_todos`) are designed for iterative task improvement workflows where you want to gradually enhance your todo items using AI assistance.

### Use Cases

**Incremental Task Enhancement**
- Pull 5 random todos from your Inbox to add better descriptions, break down into subtasks, or estimate time requirements
- Sample from your Anytime list to identify tasks that could benefit from better scheduling or prioritization
- Avoid downloading hundreds of tasks into context when you only need a few

**Content Enrichment**
- Add or improve context and suggest more actionable language
- Add context, dependencies, or next steps to existing todos
- Standardize formatting across your task descriptions
- Find tasks that might be too vague or overly complex
- Discover todos that could be automated or delegated

## Development

This project uses `pyproject.toml` to manage dependencies and build configuration. It's built using the [Model Context Protocol](https://modelcontextprotocol.io), which allows Claude to securely access tools and data.

### Development Workflow

#### Setting up a development environment

```bash
# Clone the repository
git clone https://github.com/rossshannon/Things3-MCP
cd Things3-MCP

# Set up a virtual environment with development dependencies
uv venv
uv pip install -e ".[dev]"  # Install in development mode with extra dependencies
```

#### Testing changes during development

Run the comprehensive test suite to ensure everything is working as expected:

```bash
# Run all tests (116 tests, ~3-4 minutes)
uv run pytest

# Run tests with coverage report
uv run pytest --cov=things3_mcp --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_list_assignment_operations.py

# Run tests with minimal output
uv run pytest -q

# Run tests matching a pattern
uv run pytest -k "error_handling"
```

**Test Configuration:**
- **116 comprehensive tests** covering all functionality
- **Automatic cleanup** - tests don't affect your existing Things data
- **Edge case coverage** - malformed UUIDs, timeouts, error conditions
- **Integration testing** - tests against real Things app

The tests clean up after themselves and don't affect your existing data, so you can run them as often as you like.

![Things 3 MCP Test Suite](https://github.com/rossshannon/Things3-MCP/raw/main/docs/images/Things3-mcp-test-suite.png)

## Troubleshooting

The server includes error handling for:
- Invalid UUIDs
- Missing required parameters
- Things database access errors
- Data formatting errors
- Authentication token issues
- AppleScript execution failures

### Common Issues

1. **Things app not running**: Make sure the Things app is running on your Mac for AppleScript methods to work.

### Checking Logs

All errors are logged and returned with descriptive messages. To review the MCP logs:

```bash
# Follow main logs in real-time
tail -f ~/.things-mcp/logs/things3_mcp.log

# Check error logs
tail -f ~/.things-mcp/logs/things3_mcp_errors.log

# View structured logs for analysis
cat ~/.things-mcp/logs/things3_mcp_structured.json | jq

# Claude Desktop MCP logs
tail -n 20 -f ~/Library/Logs/Claude/mcp*.log
```

## Acknowledgements

This MCP server was originally based on the Applescript bridge method from [things-mcp](https://github.com/excelsier/things-fastmcp) by [excelsier](https://github.com/excelsier/), which was in turn based on [things-mcp](https://github.com/hald/things-mcp) by [hald](https://github.com/hald/).
