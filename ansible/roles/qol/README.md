# Quality of Life (QoL) Role

Installs and configures shell and editor tooling. Sets up zsh with Oh My Zsh, Neovim with Vundle, and modern CLI tools (fzf, ripgrep, fd).

## What This Role Manages

### Shell Configuration
- Zsh installation and set as default shell
- Oh My Zsh framework with Risto theme
- 20 Oh My Zsh plugins:
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

# Oh My Zsh theme
omz_theme: risto

# Oh My Zsh plugins (20 total)
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
```

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
2. Check current shell for admin user
3. Set zsh as default shell (if not already)
4. Check if Oh My Zsh is installed
5. Install Oh My Zsh (unattended, if not present)
6. Deploy .zshrc configuration with theme and plugins
7. Deploy .zprofile (PATH configuration)
8. Deploy .alias.zsh (custom aliases)
9. Deploy .local.zsh (local overrides, force: false)
10. Include neovim setup tasks:
    ├─ Create ~/.config/nvim directory
    ├─ Create ~/.vim/bundle directory
    ├─ Clone Vundle plugin manager
    ├─ Deploy init.vim configuration
    └─ Install Vundle plugins (headless)
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
- Shell change only occurs if not already zsh
- Oh My Zsh installation uses `creates` parameter
- .local.zsh uses `force: false` to preserve user customizations
- Vundle plugin installation uses `creates` parameter
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
