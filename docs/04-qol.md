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
ansible-playbook ansible/playbooks/site.yml --tags qol
```

### Oh My Zsh Theme

**Theme**: `risto`

Provides a clean, informative prompt with git status integration.

### Plugins

The following Oh My Zsh plugins are configured:

```yaml
qol_omz_plugins:
  - 1password          # 1Password CLI completions
  - ansible            # Ansible completions
  - command-not-found  # Suggests package for missing commands
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
  - tmux               # Tmux integration and aliases
  - vscode             # VS Code integration
```

### Custom Aliases

Located in `~/.alias.zsh`, deployed from weisssrv-lib
`ansible_collections/weisssrv/infra/roles/qol/templates/alias.zsh.j2` (the source
of truth — the listing below is a summary and can drift):

```bash
export EDITOR="nvim"

# Config editing
alias ez="$EDITOR ~/.zshrc"       # Edit Zshrc
alias ea="$EDITOR ~/.alias.zsh"   # Edit Alias
alias el="$EDITOR ~/.local.zsh"   # Edit Local
alias sz='exec zsh'               # Source Zsh

# Bookmarks
alias @tmp='cd ~/tmp'
alias @downloads='cd ~/Downloads'
alias @src='cd ~/src'
alias @repo='cd ~/src/repo'

# Directory navigation
alias ..='cd ..'           # up one directory
alias ...='cd ../..'       # up two directories
alias ....='cd ../../..'   # up three directories

# Applications
alias v='nvim'
alias vim='nvim'
alias kn='kubectl config set-context --current --namespace'
```

### Shell Files

- `~/.zshrc` - Main zsh configuration
- `~/.zprofile` - Loaded before .zshrc (PATH, environment)
- `~/.alias.zsh` - Custom aliases
- `~/.local.zsh` - Local overrides (not in git)

## Neovim Configuration

### Installation

Neovim is installed with the Vundle plugin manager (bootstrapped by the role
itself, not listed as a plugin) and these `qol_nvim_plugins` defaults:

```yaml
qol_nvim_plugins:
  - tpope/vim-fugitive           # Git integration
  - sheerun/vim-polyglot         # Language pack
  - joshdick/onedark.vim         # Color scheme (qol_nvim_colorscheme: onedark)
```

### Configuration

Located at `~/.config/nvim/init.vim`, deployed from weisssrv-lib
`ansible_collections/weisssrv/infra/roles/qol/templates/init.vim.j2`:

```vim
" Vundle bootstrap
set nocompatible
filetype off
set rtp+=~/.vim/bundle/Vundle.vim
call vundle#begin()
Plugin 'VundleVim/Vundle.vim'
" ... plugins from qol_nvim_plugins ...
call vundle#end()
filetype plugin indent on

" Editor settings
set mouse=r
syntax on
colorscheme onedark
set tabstop=4 shiftwidth=4 softtabstop=0 expandtab smarttab
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

The `qol` role installs its own `qol_packages` on top of the base set, so
`--tags qol` is standalone:

```yaml
qol_packages:
  - zsh
  - neovim          # also in base base_common_packages
  - fzf
  - ripgrep
  - fd-find
```

Oh My Zsh is installed at a pinned commit (`qol_omz_commit` in
weisssrv-lib `ansible_collections/weisssrv/infra/roles/qol/defaults/main.yml`) rather than tracking `master` — bump it
deliberately like any other version pin.

Common development tools installed by the `base` role:

```yaml
base_common_packages:
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

The deployed `~/.zprofile` is a single PATH line:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

`EDITOR` is set in `~/.alias.zsh` (`export EDITOR="nvim"`). Anything else
belongs in `~/.local.zsh` (per-host overrides, not managed by Ansible).

## Ansible Deployment

### Full QoL Setup

```bash
# Deploy to all Proxmox hosts
ansible-playbook ansible/playbooks/site.yml --tags qol

# Deploy to specific host
ansible-playbook ansible/playbooks/site.yml --tags qol --limit pve-nas-01
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
   ansible-playbook ansible/playbooks/site.yml --tags qol --limit $(hostname)
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
   git clone https://github.com/VundleVim/Vundle.vim.git ~/.vim/bundle/Vundle.vim
   ```

2. **Install plugins**:
   ```bash
   nvim +PluginInstall +qall
   ```

## Related documentation

- [docs/02 — Installation](02-install.md) (where the qol role runs in the deploy)
- [docs/12 — Runbooks](12-runbooks.md) (day-2 operations that use these aliases)

## External references

- [Oh My Zsh Documentation](https://github.com/ohmyzsh/ohmyzsh/wiki)
- [Oh My Zsh Plugins](https://github.com/ohmyzsh/ohmyzsh/wiki/Plugins)
- [Neovim Documentation](https://neovim.io/doc/)
- [Vundle Plugin Manager](https://github.com/VundleVim/Vundle.vim)
