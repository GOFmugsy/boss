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

_boss_evidence() {
  local -a items
  items=(${(f)"$(command boss evidence list 2>/dev/null | sed -E 's/^#([0-9]+) +(.*)$/\1:\2/')"})
  _describe -t evidence 'evidence' items
}

_boss_notes() {
  local -a items
  items=(${(f)"$(command boss list -n 100 2>/dev/null | sed -E 's/^#([0-9]+) +(.*)$/\1:\2/')"})
  _describe -t notes 'note' items
}

_boss() {
  local curcontext="$curcontext" state state_descr line ret=1
  typeset -A opt_args
  local images='_files -g "*.(#i)(png|jpg|jpeg|gif|webp|heic|tif|tiff|bmp)(-.)"'
  local -a evidence_opts note_opts
  evidence_opts=(
    '(-l --latest)'{-l,--latest}'[add your newest screenshot as evidence]'
    '(-p --paste)'{-p,--paste}'[add the clipboard image as evidence]'
  )
  note_opts=(
    $evidence_opts
    '*'{-m,--member}'[tag a person]:handle:_boss_handles'
    '(-k --kind)'{-k,--kind}'[type of note]:kind:(note win concern feedback goal 1on1)'
    '*'{-t,--tag}'[add a #tag]:tag:'
    "*"{-a,--attach}"[add an image file as evidence]:image:$images"
    '*'{-E,--evidence}'[link existing evidence]:evidence id:_boss_evidence'
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
        'quick:write a note in a pop-up dialog (for global hotkeys)'
        'list:print recent notes'
        'export:export notes as Markdown'
        'team:list or manage team members'
        'evidence:manage evidence (screenshots) and link it to notes'
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
            _arguments $note_opts \
              '(-e --edit)'{-e,--edit}'[write the note in $EDITOR]' \
              '*:note text:' && ret=0
          fi
          ;;
        quick)
          _arguments $note_opts && ret=0
          ;;
        evidence)
          if (( CURRENT == 2 )); then
            local -a actions
            actions=(
              'add:import image(s) into the evidence library'
              'list:list evidence'
              'link:link evidence to a note'
              'unlink:unlink evidence from a note'
              'edit:caption or tag evidence'
              'open:open evidence in your image viewer'
            )
            _describe -t actions 'evidence action' actions && ret=0
          else
            local action=$words[2]
            shift words; (( CURRENT-- ))  # complete relative to the evidence sub-command
            curcontext="${curcontext%:*}-$action:"
            case $action in
              add)
                _arguments $evidence_opts \
                  '(-c --caption)'{-c,--caption}'[what the evidence shows]:caption:' \
                  '*'{-m,--member}'[tag a person]:handle:_boss_handles' \
                  '*'{-t,--tag}'[add a #tag]:tag:' \
                  '(-n --note)'{-n,--note}'[link to note]:note id:_boss_notes' \
                  "*:image:$images" && ret=0 ;;
              list)
                _arguments '1::handle:_boss_handles' '(-u --unlinked)'{-u,--unlinked}'[only unlinked evidence]' \
                  '(-f --filter)'{-f,--filter}'[text, #tag, @handle]:filter:' && ret=0 ;;
              link|unlink)
                _arguments '1:evidence:_boss_evidence' '2:note id:_boss_notes' && ret=0 ;;
              edit)
                _arguments '1:evidence:_boss_evidence' '(-c --caption)'{-c,--caption}'[caption]:caption:' \
                  '*'{-m,--member}'[tag a person]:handle:_boss_handles' '*'{-t,--tag}'[add a #tag]:tag:' && ret=0 ;;
              open)
                _arguments '1:evidence:_boss_evidence' && ret=0 ;;
            esac
          fi
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
