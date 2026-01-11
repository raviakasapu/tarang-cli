# Future Enhancement: Interactive TUI Mode

## Overview

Add a game console-style interactive interface for CLI commands, providing discoverability for new users while keeping direct commands for power users.

## Hybrid Approach

```bash
# Power users: Direct commands (existing)
tarang config --openrouter-key KEY
tarang "fix the bug"

# New users: Interactive when no args given
tarang config   # Opens interactive menu
tarang          # Opens interactive session
tarang setup    # First-time wizard
```

## Example Screens

### Main Menu (tarang with no instruction)
```
┌─────────────────────────────────────────┐
│  Tarang - AI Coding Agent               │
├─────────────────────────────────────────┤
│                                         │
│  [N] New instruction                    │
│  [H] Help & Commands                    │
│  [C] Configuration                      │
│  [S] Status                             │
│  [Q] Quit                               │
│                                         │
└─────────────────────────────────────────┘
Press a key or use ↑↓ arrows: _
```

### Configuration Menu (tarang config)
```
┌─────────────────────────────────────────┐
│  Configuration                          │
├─────────────────────────────────────────┤
│                                         │
│  [1] Set OpenRouter Key      ✓ Set      │
│  [2] Set Backend URL         Default    │
│  [3] View Current Config                │
│  [4] Reset to Defaults                  │
│                                         │
│  [X] Back to Main Menu                  │
│                                         │
└─────────────────────────────────────────┘
Select option: _
```

### Help Menu (tarang help)
```
┌─────────────────────────────────────────┐
│  Help & Commands                        │
├─────────────────────────────────────────┤
│                                         │
│  [C] Available Commands                 │
│  [E] Example Instructions               │
│  [T] Tips & Tricks                      │
│  [D] Documentation (opens browser)      │
│                                         │
│  [X] Back    [N] Next    [P] Previous   │
│                                         │
└─────────────────────────────────────────┘
```

### First-Time Setup Wizard (tarang setup)
```
┌─────────────────────────────────────────┐
│  Welcome to Tarang! 🎉                  │
│  Let's get you set up.                  │
├─────────────────────────────────────────┤
│                                         │
│  Step 1 of 3: Authentication            │
│                                         │
│  [G] Login with GitHub                  │
│  [S] Skip for now                       │
│                                         │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Step 2 of 3: OpenRouter API Key        │
├─────────────────────────────────────────┤
│                                         │
│  Enter your OpenRouter API key:         │
│  > sk-or-v1-________________            │
│                                         │
│  [?] How to get a key                   │
│  [S] Skip for now                       │
│                                         │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Step 3 of 3: Ready!                    │
├─────────────────────────────────────────┤
│                                         │
│  ✓ GitHub authenticated                 │
│  ✓ OpenRouter key configured            │
│                                         │
│  You're all set! Try:                   │
│    tarang "explain the project"         │
│                                         │
│  [Enter] Start using Tarang             │
│                                         │
└─────────────────────────────────────────┘
```

## Implementation Notes

### Libraries
- `textual` - Full TUI framework (by Rich creator)
- `questionary` - Simple interactive prompts
- `rich` - Already a dependency, has basic prompts

### Key Principles
1. **Always keep direct commands** - Never break scriptability
2. **Interactive = fallback** - Only when args not provided
3. **Keyboard first** - Single key shortcuts (1, 2, Q, X)
4. **Arrow navigation** - For accessibility
5. **Escape = back** - Consistent exit pattern

### Files to Create
```
src/tarang/tui/
├── __init__.py
├── app.py           # Main TUI application
├── screens/
│   ├── main.py      # Main menu
│   ├── config.py    # Configuration screen
│   ├── help.py      # Help screen
│   └── setup.py     # First-time wizard
└── widgets/
    ├── menu.py      # Reusable menu widget
    └── input.py     # Styled input widget
```

### Integration Points
```python
# In cli.py

@cli.command()
def config(...):
    if no_args_provided:
        # Launch interactive TUI
        from tarang.tui.screens.config import ConfigScreen
        ConfigScreen().run()
    else:
        # Direct command execution
        ...

@cli.command()
def setup():
    """First-time setup wizard."""
    from tarang.tui.screens.setup import SetupWizard
    SetupWizard().run()
```

## Priority

**Low** - Nice to have, not essential for core functionality.

Implement after:
1. Hybrid WebSocket architecture (PRD-8)
2. Job/milestone management
3. Resume capability

## References

- Textual docs: https://textual.textualize.io/
- Rich prompts: https://rich.readthedocs.io/en/latest/prompt.html
- Example TUI: lazygit, htop, k9s
