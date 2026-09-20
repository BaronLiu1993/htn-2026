if not contains "$HOME/Documents/htn-2026/.tools/bin" $PATH
    # Prepending path in case a system-installed binary needs to be overridden
    set -x PATH "$HOME/Documents/htn-2026/.tools/bin" $PATH
end
