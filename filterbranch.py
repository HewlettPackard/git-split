# filterbranch
#
# module to handle filtering of branches to remove files and eliminate
# empty commits including merges. Based on cj-git-filter-branch from
# https://github.com/pflanze/chj-bin/

import logging


class FilterBranch:

    tag_filter = '''cat'''
    index_filter = '''git ls-files -z | grep -z -v %s | xargs -0 --no-run-if-empty git rm --cached'''
    commit_filter = '''
DEBUG_LVL=%d
DEBUG_LVL=${DEBUG_LVL:-0}

AUTHORS_FILE=%s

function log_warn() {
    [ "${DEBUG_LVL}" -ge 3 -a "$*" != "" ] && echo "$*" >&2
}

function log_info() {
    [ "${DEBUG_LVL}" -ge 2 -a "$*" != "" ] && echo "$*" >&2
}

function log_debug() {
    [ "${DEBUG_LVL}" -ge 1 -a "$*" != "" ] && echo "$*" >&2
}

log_debug "args = $@"
log_debug "$(git ls-files)"

if [ -n "${AUTHORS_FILE}" ] && [ -e "${AUTHORS_FILE}" ]
then

    for i in AUTHOR COMMITTER
    do
        _NAME="$(awk -F ":" '
{
    if ( match($1,ENVIRON["GIT_'${i}'_NAME"]) ) {
        if ( !match("", $3) ) {
            print $3
        } else {
            print $1
        }
    }
}
' < ${AUTHORS_FILE} )"

        _EMAIL="$(awk -F ":" '
{
    if ( match($1,ENVIRON["GIT_'${i}'_NAME"]) ) {
        if ( !match("", $3) ) {
            print $4
        } else {
            print $2
        }
    }
}
' < ${AUTHORS_FILE} )"
        if [ -n "${_NAME}" ]
        then
            eval 'export GIT_${i}_NAME="${_NAME}"'
            eval 'export GIT_${i}_EMAIL="${_EMAIL}"'
        fi
    done
fi

if [ initial = "${3-initial}" ]
then
    # empty tree in git always hashes to "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    if [ "$1" = "4b825dc642cb6eb9a060e54bf8d69288fbee4904" ]
    then
        log_info "skipped empty tree"
        skip_commit "$@"
    else
        log_warn "initial commit"
        git_commit_non_empty_tree "$@"
    fi
else
    if [ znot_a_merge = z"${5-not_a_merge}" ]
    then
        if [ "$1" = "$(git rev-parse "$3"^{tree})" ]
        then
            log_info "skipped"
            skip_commit "$@"
        else
            log_info "committed (if non empty) - not a merge"
            git_commit_non_empty_tree "$@"
        fi
    else
        log_warn "seeing a merge: $@"
        my_tree="$1"
        shift

        tmpd="/tmp/cj-git-filter-branch.$$"
        (
            umask 0077
            mkdir "$tmpd"
        )
        declare -a allparents=()
        for p in "$@"
        do
            # eliminate "-p"s
            if [ ! x"$p" = x"-p" ]
            then
                # eliminate parent doubles
                if [ ! -e "$tmpd/$p" ]
                then
                    touch "$tmpd/$p"
                    allparents+=("$p")
                fi
            fi
        done
        rm -rf "$tmpd"

        declare -a sametreeparents=()
        declare -a notsametreeparents=()
        for p in "${allparents[@]}"
        do
            if [ "$my_tree" = "$(git rev-parse "$p"^{tree})" ]
            then
                sametreeparents+=("$p")
            else
                notsametreeparents+=("$p")
            fi
        done

        log_debug "sametreeparents=${sametreeparents[@]}"
        log_debug "notsametreeparents=${notsametreeparents[@]}"
        if [ "${#sametreeparents[@]}" -ge 1 ]
        then
            declare -a neededparents=()
            for sametreeparent in "${sametreeparents[0]}"
            do
                for p in "${notsametreeparents[@]}"
                do
                    set +e
                    mb=$(git merge-base "$sametreeparent" "$p")
                    rc=$?
                    set -e
                    if [ $rc = 0 ]
                    then
                        if [ ! "$mb" = "$p" ]
                        then
                            neededparents+=("$p")
                        fi
                    elif [ $rc = 1 ]
                    then
                        log_warn "non-common ancestors, keeping merge"
                        neededparents+=("$p")
                    else
                        die "git merge-base had a problem"
                    fi
                done
            done

            if [ "${#neededparents[@]}" = 0 ]
            then
                log_info "skipped - no parents needed"
                log_debug "skip_commit (#neededparents[@] == 0)\"$my_tree\" -p \"$sametreeparent\""
                skip_commit "$my_tree" -p "$sametreeparent"
            else
                # still drop the parents that have no effect, ok?
                declare -a newargs=(-p "$sametreeparent")
                for p in "${neededparents[@]}"
                do
                    newargs+=(-p)
                    newargs+=("$p")
                done
                log_info "committed (if non empty) sametreeparents == 1" >&2
                log_debug "git_commit_non_empty_tree \"$my_tree\" \"${newargs[@]}\""
                git_commit_non_empty_tree "$my_tree" "${newargs[@]}"
            fi
        else
            log_info "committed (if non empty) sametreeparents >1"
            log_debug "git_commit_non_empty_tree \"$my_tree\" \"$@\""
            # (could have doubles in $@ ? anyway, let git get rid of them)
            git_commit_non_empty_tree "$my_tree" "$@"
        fi
    fi
fi
'''

    debuglvls = {
                 logging.WARN: 3,
                 logging.INFO: 2,
                 logging.DEBUG: 1,
                 logging.NOTSET: 0,
                }

    def __init__(self, debuglvl=logging.WARN):
        if debuglvl > logging.WARN:
            debuglvl = logging.WARN

        self.debuglvl = debuglvls(debuglvl)
