# Quality of Life (QoL) Role

Installs and configures shell and editor tooling. Sets up zsh with Oh My Zsh, Neovim with Vundle, and modern CLI tools (fzf, ripgrep, fd).

## What This Role Manages

### Shell Configuration
- Zsh installation and set as default shell
- Oh My Zsh framework with Risto theme
- 15 Oh My Zsh plugins:
  - Development: ansible, docker, docker-compose, kubectl, terraform, golang, vscode
  - Productivity: git, fzf, tmux, rsync, systemd
  - Utilities: 1password, command-not-found, dotenv
- Custom alias file (`.alias.zsh`)
- Local overrides file (`.local.zsh`, not tracked)
- PATH configuration (`.zprofile`)

### Modern CLI Tools
- fzf - Fuzzy finder for command history and file searching
- ripgrep - Fast grep alternative
- fd-find - Fast find alternative
- neovim - Modern vim fork

### Neovim Configuration
- Vundle plugin manager installation
- Preconfigured plugins:
  - vim-fugitive (Git integration)
  - vim-polyglot (Language pack)
  - onedark.vim (Color scheme)
- XDG-compliant config location (`~/.config/nvim/init.vim`)
- Automatic plugin installation

## Configuration

### Default Variables

```yaml
# Admin user (from base role)
admin_user: eric

# Oh My Zsh pinned commit — the framework clone is checked out at this commit
# and self-update is disabled in .zshrc. Update by picking a new commit from
# https://github.com/ohmyzsh/ohmyzsh/commits/master and bumping the pin.
omz_commit: "061f773dd356df52a8bccd5e73377c012f97ef14"

# Oh My Zsh theme
omz_theme: risto

# Oh My Zsh plugins (15 total)
omz_plugins:
  - 1password
  - ansible
  - command-not-found
  - docker
  - docker-compose
  - dotenv
  - fzf
  - git
  - golang
  - kubectl
  - rsync
  - systemd
  - terraform
  - tmux
  - vscode

# Neovim plugins
nvim_plugins:
  - tpope/vim-fugitive      # Git integration
  - sheerun/vim-polyglot    # Language pack
  - joshdick/onedark.vim    # Color scheme

# Colorscheme applied in init.vim (with `silent!`, so a missing scheme
# degrades to the default look). Must come from nvim_plugins or be built in.
nvim_colorscheme: onedark
```

Vundle itself is pinned (v0.10.2), but `+PluginInstall` clones the plugins in
`nvim_plugins` at their current HEAD — plugin contents are not reproducible
across hosts/time. Pin via forks or explicit checkouts if that ever matters.

### Customization

Override in `group_vars` or `host_vars`:

```yaml
# Use a different theme
omz_theme: agnoster

# Add additional plugins
omz_plugins:
  - aws
  - vagrant
  - python

# Add neovim plugins
nvim_plugins:
  - preservim/nerdtree
  - airblade/vim-gitgutter
```

Only OMZ-bundled plugins are supported in `omz_plugins` — the role templates
the `plugins=()` array but does not clone external plugins (e.g.
`zsh-autosuggestions`), so listing one would emit "plugin not found" at shell
startup.

## Deployment

```bash
# Deploy to Proxmox hosts (typical use case)
ansible-playbook ansible/playbooks/site.yml --tags qol

# Deploy to specific host
ansible-playbook ansible/playbooks/site.yml --limit pve-nas-01 --tags qol
```

## Architecture

Applied to Proxmox hosts via the main site playbook:

```yaml
- name: Deploy Proxmox host configuration
  hosts: proxmox
  roles:
    - qol
```

Can also be applied to k3s nodes:

```yaml
- name: Deploy base configuration to k3s nodes
  hosts: k3s
  roles:
    - qol
```

## Task Flow

```
1. Install QoL packages (zsh, neovim, fzf, ripgrep, fd-find)
2. Set zsh as default shell (unconditional; user module is idempotent)
3. Remove a legacy shallow install.sh clone of Oh My Zsh (one-time migration)
4. Clone/converge Oh My Zsh at the pinned commit (omz_commit)
5. Deploy .zshrc configuration with theme and plugins (self-update disabled)
6. Deploy .zprofile (PATH configuration)
7. Deploy .alias.zsh (custom aliases)
8. Deploy .local.zsh (local overrides, force: false)
9. Include neovim setup tasks:
    ├─ Create ~/.config/nvim directory
    ├─ Create ~/.vim/bundle directory
    ├─ Clone Vundle plugin manager (pinned v0.10.2)
    ├─ Deploy init.vim configuration
    └─ Install Vundle plugins (headless, at plugin HEAD)
```

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/neovim.yml` - Neovim and Vundle setup
- `templates/zshrc.j2` - Oh My Zsh configuration
- `templates/alias.zsh.j2` - Custom shell aliases
- `templates/init.vim.j2` - Neovim configuration
- `defaults/main.yml` - Default variables

## Dependencies

- `base` role (provides admin user and common packages)

## Idempotency

- Package installation is idempotent
- Shell is set unconditionally; the user module reports changed only on a real change
- Oh My Zsh converges to the pinned commit via the git module (no change once there)
- .local.zsh uses `force: false` to preserve user customizations
- Vundle plugin installation uses `creates` (marker keyed to the plugin-list hash)
- Configuration files are templates (idempotent overwrites)

## Security

- All operations run as admin user (not root)
- Oh My Zsh installed from official repository
- Vundle cloned with depth=1 for minimal attack surface
- No sudo password required (passwordless sudo from base role)

## Customization Examples

### Adding Custom Aliases

Edit your local `group_vars` or modify the template:

```bash
# In .alias.zsh
alias kc='kubectl'
alias tf='terraform'
alias ans='ansible-playbook'
```

### Using a Different Theme

```yaml
omz_theme: agnoster  # Popular powerline theme
omz_theme: robbyrussell  # Oh My Zsh default
omz_theme: powerlevel10k/powerlevel10k  # Requires additional setup
```

### Adding Language-Specific Plugins

```yaml
omz_plugins:
  - python
  - pip
  - virtualenv
  - node
  - npm
  - rust
```
