# Tarang CLI

AI-powered coding assistant with ManagerAgent architecture.

## Installation

```bash
pip install tarang
```

## Quick Start

```bash
# Start interactive session
tarang run

# Run a single instruction
tarang run "create a hello world app"

# Run and exit
tarang run "fix linter errors" --once
```

## Commands

- `tarang run [instruction]` - Start coding session (interactive or single)
- `tarang init <project>` - Initialize a new project
- `tarang chat` - Interactive chat mode
- `tarang status` - Show project status
- `tarang resume` - Resume interrupted execution
- `tarang reset` - Reset execution state
- `tarang clean` - Remove all Tarang state
- `tarang check` - Verify configuration

## Options

- `--project-dir, -p` - Project directory (default: current)
- `--config, -c` - Agent config (coder, explorer, orchestrator)
- `--verbose, -v` - Enable verbose output
- `--once` - Run single instruction and exit

## Configuration

Tarang requires an OpenRouter API key:

```bash
export OPENROUTER_API_KEY=your_key
```

## Project State

Tarang stores execution state in `.tarang/` directory:
- `state.json` - Current execution state
- Supports resume after interruption

## Links

- Website: [devtarang.ai](https://devtarang.ai)
- Documentation: [docs.devtarang.ai](https://docs.devtarang.ai)

## License

MIT
