#!/bin/zsh
# Install boss for the current user on macOS (zsh).
#
#   ./scripts/install-macos.zsh            # venv + `boss` on your PATH, prints ~/.zshrc lines
#   ./scripts/install-macos.zsh --zshrc    # ...and append those lines to ~/.zshrc (once)
#
# Environment: PYTHON (default python3), BOSS_BIN_DIR (default ~/.local/bin).
emulate -L zsh
setopt err_exit no_unset pipe_fail

repo=${0:A:h:h}
python=${PYTHON:-python3}
bin_dir=${BOSS_BIN_DIR:-$HOME/.local/bin}
edit_zshrc=false
[[ ${1:-} == --zshrc ]] && edit_zshrc=true

if ! command -v $python >/dev/null; then
  print -u2 "boss: $python not found. Install Python 3.10+ (e.g. 'brew install python') and retry."
  exit 1
fi
if ! $python -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  print -u2 "boss: needs Python 3.10+, found $($python --version 2>&1). Set PYTHON=/path/to/python3.x"
  exit 1
fi

print "• Creating virtualenv in $repo/.venv"
[[ -x $repo/.venv/bin/python ]] || $python -m venv $repo/.venv
$repo/.venv/bin/python -m pip install --quiet --upgrade pip
print "• Installing boss (with inline image previews)"
$repo/.venv/bin/python -m pip install --quiet -e "${repo}[images]"

mkdir -p $bin_dir
ln -sf $repo/.venv/bin/boss $bin_dir/boss
print "• Linked $bin_dir/boss"

zshrc_lines=()
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  zshrc_lines+=("export PATH=\"${bin_dir/#$HOME/\$HOME}:\$PATH\"")
fi
zshrc_lines+=('eval "$(boss init zsh)"   # boss: tab completion + noglob alias')

zshrc=${ZDOTDIR:-$HOME}/.zshrc
if $edit_zshrc; then
  if [[ -f $zshrc ]] && grep -q 'boss init zsh' $zshrc; then
    print "• $zshrc already loads boss — left unchanged"
  else
    print -l "" "# boss (performance notes)" $zshrc_lines >> $zshrc
    print "• Added boss to $zshrc — open a new terminal or run: source $zshrc"
  fi
else
  print "\nAdd these lines to $zshrc (after compinit, if you call it), or re-run with --zshrc:\n"
  print -l "    "$^zshrc_lines
fi

cat <<EOF

Done. Try:
    boss                      # open the app (tip: tmux new -As boss boss)
    boss team add alice "Alice Smith"
    boss add                  # write a note in \$EDITOR (${VISUAL:-${EDITOR:-vim}}); :wq saves, :q! cancels

The first screenshot asks macOS for Screen Recording permission for your terminal app
(System Settings → Privacy & Security → Screen & System Audio Recording).
EOF
