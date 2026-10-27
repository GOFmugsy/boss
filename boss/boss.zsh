# boss — zsh integration.  Add this line to ~/.zshrc (after compinit, if you call it):
#
#     eval "$(boss init zsh)"
#
# * `noglob` stops zsh from treating ? * [ ] in note text as file globs
#   (otherwise `boss add great job today?` fails with "no matches found").
# * Tab completion for commands, options, kinds and @handles of your team.
#
# Still quote notes containing #tags or ! with single quotes:  boss add '@bob shipped it! #win'
# (zsh drops an unquoted #word as a comment when interactivecomments is set, and expands
# ! inside double quotes), or use `-t win`, or just run `boss add` to write it in $EDITOR.

alias boss='noglob boss'

_boss_handles() {
  local -a handles
  handles=(${(f)"$(command boss team --handles 2>/dev/null)"})
  compadd "$@" -a handles
}

_boss() {
  local curcontext="$curcontext" state state_descr line ret=1
  typeset -A opt_args
  local -a capture
  capture=(
    '*'{-m,--member}'[tag a person]:handle:_boss_handles'
    '(-k --kind)'{-k,--kind}'[type of note]:kind:(note win concern feedback goal 1on1)'
    '*'{-t,--tag}'[add a #tag]:tag:'
    '(-s --shot)'{-s,--shot}'[capture a screen region and attach it]'
    '(-p --paste)'{-p,--paste}'[attach the image on the clipboard]'
    '*'{-a,--attach}'[attach an image file]:image:_files -g "*.(png|jpg|jpeg|gif|webp|heic)(-.)"'
  )

  _arguments -C \
    '--home[data directory]:directory:_files -/' \
    '(-h --help)'{-h,--help}'[show help]' \
    '1:command:->command' \
    '*::argument:->argument' && ret=0

  case $state in
    command)
      local -a commands
      commands=(
        'add:add a note (no text opens $EDITOR)'
        'quick:capture a note with a pop-up dialog (for global hotkeys)'
        'list:print recent notes'
        'export:export notes as Markdown'
        'team:list or manage team members'
        'init:print shell integration'
        'where:print the data directory'
      )
      _describe -t commands 'boss command' commands && ret=0
      ;;
    argument)
      curcontext="${curcontext%:*:*}:boss-$line[1]:"
      case $line[1] in
        add)
          if [[ $PREFIX == @* ]]; then
            compset -P '@'
            _boss_handles -S ' ' && ret=0
          else
            _arguments $capture \
              '(-e --edit)'{-e,--edit}'[write the note in $EDITOR]' \
              '*:note text:' && ret=0
          fi
          ;;
        quick)
          _arguments $capture && ret=0
          ;;
        list)
          _arguments \
            '1::handle:_boss_handles' \
            '(-f --filter)'{-f,--filter}'[text, #tag, @handle, kind:win]:filter:' \
            '--since[only notes since]:date (YYYY-MM-DD):' \
            '(-n --limit)'{-n,--limit}'[maximum notes]:count:' && ret=0
          ;;
        export)
          _arguments \
            '1::handle:_boss_handles' \
            '(-o --output)'{-o,--output}'[file to write]:file:_files -g "*.md(-.)"' \
            '(-f --filter)'{-f,--filter}'[text, #tag, @handle, kind:win]:filter:' \
            '--since[only notes since]:date (YYYY-MM-DD):' \
            '--until[only notes before]:date (YYYY-MM-DD):' && ret=0
          ;;
        team)
          _arguments \
            '1:action:(list add archive restore)' \
            '2::handle:_boss_handles' \
            '*::name:' \
            '--role[role or level]:role:' && ret=0
          ;;
        init)
          _arguments '1:shell:(zsh)' && ret=0
          ;;
      esac
      ;;
  esac
  return ret
}

if (( $+functions[compdef] )); then
  compdef _boss boss
fi
