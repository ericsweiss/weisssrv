# Quality of Life (QoL) Configuration

This document covers shell customizations, editor setup, and developer tools configured on Proxmox hosts.

## Overview

The `qol` role configures a comfortable development environment for the `eric` user on all Proxmox hosts.

**Components**:
- zsh + Oh My Zsh
- Neovim + Vundle plugins
- Common development tools
- Custom aliases and shell functions

## Shell Configuration (zsh + Oh My Zsh)

### Installation

Applied by the `qol` Ansible role:

```bash
ansible-playbook ansible/playbooks/base.yml --tags qol
```

### Oh My Zsh Theme

**Theme**: `risto`

Provides a clean, informative prompt with git status integration.

### Plugins

The following Oh My Zsh plugins are configured:

```yaml
omz_plugins:
  - 1password          # 1Password CLI completions
  - ansible            # Ansible completions
  - command-not-found  # Suggests package for missing commands
  - direnv             # Directory-specific environment variables
  - docker             # Docker completions
  - docker-compose     # Docker Compose completions
  - dotenv             # Auto-load .env files
  - fzf                # Fuzzy finder integration
  - git                # Git aliases and completions
  - golang             # Go development tools
  - kubectl            # Kubernetes CLI completions
  - rsync              # Rsync completions
  - systemd            # Systemd unit management shortcuts
  - terraform          # Terraform completions
  - thefuck            # Corrects previous console command
  - tmux               # Tmux integration and aliases
  - vscode             # VS Code integration
  - zsh-autosuggestions  # Fish-like autosuggestions
  - zsh-syntax-highlighting  # Syntax highlighting
```

### Custom Aliases

Located in `~/.alias.zsh`:

```bash
# Navigation
alias ..="cd .."
alias ...="cd ../.."

# List variants
alias ll="ls -lah"
alias la="ls -A"

# Git shortcuts
alias gs="git status"
alias gp="git pull"
alias gc="git commit"

# Config editing
alias ez="$EDITOR ~/.zshrc"
alias ea="$EDITOR ~/.alias.zsh"
alias el="$EDITOR ~/.local.zsh"
alias sz="source ~/.zshrc"

# System shortcuts
alias update="sudo apt update && sudo apt upgrade -y"
alias ports="sudo netstat -tulanp"
```

### Shell Files

- `~/.zshrc` - Main zsh configuration
- `~/.zprofile` - Loaded before .zshrc (PATH, environment)
- `~/.alias.zsh` - Custom aliases
- `~/.local.zsh` - Local overrides (not in git)

## Neovim Configuration

### Installation

Neovim is installed with Vundle plugin manager and common plugins:

```yaml
nvim_plugins:
  - VundleVim/Vundle.vim         # Plugin manager
  - tpope/vim-fugitive           # Git integration
  - sheerun/vim-polyglot         # Language pack
  - joshdick/onedark.vim         # Color scheme
```

### Configuration

Located at `~/.config/nvim/init.vim`:

```vim
" Enable line numbers
set number

" Enable syntax highlighting
syntax on

" Set color scheme
colorscheme onedark

" Enable mouse support
set mouse=a

" Tab settings
set tabstop=2
set shiftwidth=2
set expandtab

" Search settings
set ignorecase
set smartcase
set hlsearch
set incsearch
```

### Installing Additional Plugins

1. Edit `~/.config/nvim/init.vim` and add plugin:
   ```vim
   Plugin 'user/plugin-name'
   ```

2. Install via Vundle:
   ```bash
   nvim +PluginInstall +qall
   ```

## Development Tools

### Installed Packages

Common development tools installed by the `base` role:

```yaml
common_packages:
  - curl
  - wget
  - neovim
  - htop
  - tmux
  - screen
  - git
  - jq              # JSON processor
  - unzip
  - rsync
  - net-tools       # ifconfig, netstat, etc.
  - dnsutils        # dig, nslookup, etc.
  - ca-certificates
  - gnupg
  - lsb-release
  - sudo
  - pciutils        # lspci (e1000e NIC detection workaround)
```

### tmux

Terminal multiplexer for managing multiple terminal sessions.

**Basic usage**:
```bash
# Start new session
tmux

# Detach: Ctrl+b, then d
# List sessions
tmux ls

# Attach to session
tmux attach -t 0
```

### fzf (Fuzzy Finder)

Fast file and command finder with Oh My Zsh integration.

**Basic usage**:
```bash
# Fuzzy find files
Ctrl+T

# Fuzzy find command history
Ctrl+R

# Fuzzy cd into directory
Alt+C
```

## Custom Configuration

### Per-Host Overrides

Use `~/.local.zsh` for host-specific configuration:

```bash
# On pve-nas-01
echo "export NAS_ROOT=/mnt/tank" >> ~/.local.zsh

# On pve-opt-03
echo "export COMPUTE_MODE=1" >> ~/.local.zsh
```

### Updating Configuration

After modifying shell configuration:

```bash
# Reload zsh
source ~/.zshrc

# Or use alias
sz
```

## Environment Variables

Common environment variables set in `~/.zprofile`:

```bash
# Default editor
export EDITOR=nvim
export VISUAL=nvim

# PATH additions
export PATH="$HOME/.local/bin:$PATH"

# Go development
export GOPATH="$HOME/go"
export PATH="$GOPATH/bin:$PATH"

# 1Password CLI
export OP_ACCOUNT="my.1password.com"
```

## Ansible Deployment

### Full QoL Setup

```bash
# Deploy to all Proxmox hosts
ansible-playbook ansible/playbooks/base.yml --tags qol

# Deploy to specific host
ansible-playbook ansible/playbooks/base.yml --tags qol --limit pve-nas-01
```

### Individual Components

```bash
# Install Oh My Zsh only
ansible-playbook ansible/playbooks/base.yml --tags omz

# Install Neovim config only
ansible-playbook ansible/playbooks/base.yml --tags nvim
```

## Troubleshooting

### Oh My Zsh Not Loading

1. **Verify installation**:
   ```bash
   ls -la ~/.oh-my-zsh
   ```

2. **Check .zshrc sources it**:
   ```bash
   grep "oh-my-zsh.sh" ~/.zshrc
   ```

3. **Re-run role**:
   ```bash
   ansible-playbook ansible/playbooks/base.yml --tags qol --limit $(hostname)
   ```

### Plugin Not Working

1. **Verify plugin is enabled**:
   ```bash
   grep "plugins=" ~/.zshrc
   ```

2. **Reload shell**:
   ```bash
   source ~/.zshrc
   ```

3. **Check plugin exists**:
   ```bash
   ls ~/.oh-my-zsh/plugins/plugin-name
   # Or for custom plugins
   ls ~/.oh-my-zsh/custom/plugins/plugin-name
   ```

### Neovim Plugins Not Loaded

1. **Install Vundle**:
   ```bash
   git clone https://github.com/VundleVim/Vundle.vim.git ~/.config/nvim/bundle/Vundle.vim
   ```

2. **Install plugins**:
   ```bash
   nvim +PluginInstall +qall
   ```

### Command Not Found (thefuck)

The `thefuck` plugin requires installation:

```bash
sudo apt install thefuck
# Or via pip
pip3 install thefuck
```

Then restart shell.

## References

- [Oh My Zsh Documentation](https://github.com/ohmyzsh/ohmyzsh/wiki)
- [Oh My Zsh Plugins](https://github.com/ohmyzsh/ohmyzsh/wiki/Plugins)
- [Neovim Documentation](https://neovim.io/doc/)
- [Vundle Plugin Manager](https://github.com/VundleVim/Vundle.vim)
