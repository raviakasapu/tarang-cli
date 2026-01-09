# Tarang CLI

AI-powered coding assistant - Privacy-first, high-accuracy code generation.

## Installation

```bash
pip install tarang
```

## Quick Start

1. **Initialize your project:**
   ```bash
   tarang init
   ```

2. **Start a session:**
   ```bash
   tarang
   ```

3. **Enter your coding request:**
   ```
   > Add a dark mode toggle to the settings page
   ```

## Commands

- `tarang` - Start an interactive coding session
- `tarang init` - Initialize Tarang for the current project
- `tarang login` - Authenticate with devtarang.ai
- `tarang status` - Show current configuration status
- `tarang ask "question"` - Ask a quick question

## Options

- `--no-lint` - Skip shadow linting verification
- `--dry-run` - Show changes without applying them
- `--verbose` - Enable verbose output

## Configuration

Tarang uses two configuration files:

- **Global config:** `~/.tarang/config.json` - API keys and preferences
- **Project config:** `.tarang/project.json` - Project-specific settings

## BYOK (Bring Your Own Key)

Tarang uses OpenRouter for LLM access. Get your API key at [openrouter.ai/keys](https://openrouter.ai/keys).

## Privacy

- Your code never leaves your machine for storage
- Only necessary context is sent for processing
- All processing happens in-memory

## Links

- Website: [devtarang.ai](https://devtarang.ai)
- Documentation: [docs.devtarang.ai](https://docs.devtarang.ai)
