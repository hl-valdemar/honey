#!/bin/sh
set -eu

fail() { printf 'honey install: %s\n' "$*" >&2; exit 1; }

[ "$#" -le 1 ] || fail 'usage: sh install.sh [install|rollback]'
action=${1:-install}
case "$action" in install|rollback) ;; *) fail 'usage: sh install.sh [install|rollback]' ;; esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
prefix=${PREFIX:-"$HOME/.local"}
mkdir -p "$prefix/bin" "$prefix/opt/honey"
prefix=$(CDPATH= cd -- "$prefix" && pwd -P)
versions=$(CDPATH= cd -- "$prefix/opt/honey" && pwd -P)
launcher=$prefix/bin/honey
previous=$versions/previous
lock=$versions/.install-lock
mkdir "$lock" 2>/dev/null || fail "installation is locked; check for another installer before removing $lock"

release=
activation=
cleanup() {
    status=$?
    trap - 0 HUP INT TERM
    if [ -n "$release" ]; then rm -rf -- "$release"; fi
    if [ -n "$activation" ]; then rm -rf -- "$activation"; fi
    rm -rf -- "$lock"
    exit "$status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Never replace an unrelated executable or move links into a destination directory.
for pointer in "$launcher" "$previous"; do
    if [ -e "$pointer" ] && [ ! -L "$pointer" ]; then fail "refusing to replace $pointer (not a symlink)"; fi
    [ ! -d "$pointer" ] || fail "refusing to replace directory $pointer"
done
managed_target() {
    directory=${1%/bin/honey}
    [ "$directory" != "$1" ] && [ "${directory%/*}" = "$versions" ] || fail "unmanaged compiler target: $1"
    case "${directory##*/}" in release.*) ;; *) fail "unmanaged compiler target: $1" ;; esac
    [ -f "$1" ] && [ -x "$1" ] || fail "compiler target is missing or not executable: $1"
}
current=
if [ -L "$launcher" ]; then
    current=$(readlink "$launcher")
    managed_target "$current"
fi

if [ "$action" = rollback ]; then
    [ -n "$current" ] && [ -L "$previous" ] || fail 'no previous installation to restore'
    target=$(readlink "$previous")
    managed_target "$target"
else
    bootstrap=${BOOTSTRAP:-"$repo/build/honey"}
    [ -f "$bootstrap" ] && [ -x "$bootstrap" ] || fail "working bootstrap compiler required: $bootstrap (set BOOTSTRAP to choose one)"
    command -v clang >/dev/null 2>&1 || fail 'Clang is required on PATH'
    release=$(mktemp -d "$versions/release.XXXXXXXX")
    mkdir "$release/bin" "$release/bootstrap" "$release/ffi"
    cp -p "$bootstrap" "$release/bootstrap/honey"
    # Build the compiler against the same library snapshot that will be installed.
    cp -RL "$repo/source" "$repo/std" "$release/"
    cp -RL "$repo/ffi/c" "$release/ffi/c"
    cp "$repo/build.hon" "$release/build.hon"
    printf 'Building two compiler generations in %s\n' "$release"
    "$release/bootstrap/honey" build "$release/source" -o "$release/bin/honey-stage1" -O2
    "$release/bin/honey-stage1" build "$release/source" -o "$release/bin/honey" -O2
    printf 'Checking compiler tests and a standalone native project\n'
    if ! (cd "$release" && "$release/bin/honey" test source) >"$lock/tests.log" 2>&1; then
        cat "$lock/tests.log" >&2
        fail 'candidate compiler tests failed; active installation unchanged'
    fi
    rm -rf -- "$release/source" "$release/build.hon" "$release/bin/honey-stage1"
    # Outside the checkout, with only the installed templates available.
    (cd "$lock" && "$release/bin/honey" new "$lock/smoke")
    "$release/bin/honey" build "$lock/smoke" -O2
    output=$("$lock/smoke/build/app")
    [ "$output" = 0 ] || fail 'candidate native project returned unexpected output'
    target=$release/bin/honey
    # A signal after activation must never delete the newly active installation.
    release=
fi

# Both destinations are file symlinks; rename switches each pointer atomically.
if [ -n "$current" ]; then
    ln -s "$current" "$lock/previous"
    mv -f "$lock/previous" "$previous"
fi
# Stage beside the launcher so rename stays atomic even if bin/ is another mount.
activation=$(mktemp -d "$prefix/bin/.honey-activate.XXXXXXXX")
ln -s "$target" "$activation/honey"
mv -f "$activation/honey" "$launcher"
printf 'Active compiler: %s\n' "$target"
printf 'Launcher: %s\n' "$launcher"
