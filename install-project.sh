#!/bin/sh
set -eu

fail() { printf 'honey install: %s\n' "$*" >&2; exit 1; }

[ "$#" -eq 2 ] || fail 'usage: sh install-project.sh <name> <executable> | rollback <name>'
action=install
if [ "$1" = rollback ]; then
    action=rollback
    name=$2
else
    name=$1
    executable=$2
fi
case "$name" in
    ''|.|..|-*|honey|rollback|*[!a-zA-Z0-9_.-]*) fail "invalid or reserved executable name: $name" ;;
esac
if [ "$action" = install ]; then
    [ -f "$executable" ] && [ -x "$executable" ] || fail "executable is missing or not executable: $executable"
fi
prefix=${PREFIX:-"$HOME/.local"}
mkdir -p "$prefix/bin" "$prefix/opt/$name"
prefix=$(CDPATH= cd -- "$prefix" && pwd -P)
versions=$(CDPATH= cd -- "$prefix/opt/$name" && pwd -P)
launcher=$prefix/bin/$name
previous=$versions/previous
lock=$versions/.install-lock
mkdir "$lock" 2>/dev/null || fail "installation is locked; check for another installer before removing $lock"

release=
activation=
cleanup() {
    status=$?
    trap - 0 HUP INT TERM
    # A signal immediately after rename must not delete the active candidate.
    if [ -n "$release" ]; then
        if [ ! -L "$launcher" ] || [ "$(readlink "$launcher")" != "$release/bin/$name" ]; then
            rm -rf -- "$release"
        fi
    fi
    if [ -n "$activation" ]; then rm -rf -- "$activation"; fi
    rm -rf -- "$lock"
    exit "$status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

managed_target() {
    directory=${1%/bin/"$name"}
    [ "$directory" != "$1" ] && [ "${directory%/*}" = "$versions" ] || fail "unmanaged executable target: $1"
    case "${directory##*/}" in release.*) ;; *) fail "unmanaged executable target: $1" ;; esac
    [ -f "$1" ] && [ -x "$1" ] || fail "executable target is missing or not executable: $1"
}
# Refuse unrelated files, directories, and links, including the rollback pointer.
for pointer in "$launcher" "$previous"; do
    if [ -e "$pointer" ] && [ ! -L "$pointer" ]; then fail "refusing to replace $pointer (not a symlink)"; fi
    [ ! -d "$pointer" ] || fail "refusing to replace directory $pointer"
    if [ -L "$pointer" ]; then managed_target "$(readlink "$pointer")"; fi
done
current=
if [ -L "$launcher" ]; then current=$(readlink "$launcher"); fi

if [ "$action" = rollback ]; then
    [ -n "$current" ] && [ -L "$previous" ] || fail 'no previous installation to restore'
    target=$(readlink "$previous")
else
    release=$(mktemp -d "$versions/release.XXXXXXXX")
    mkdir "$release/bin"
    cp -p -- "$executable" "$release/bin/$name"
    target=$release/bin/$name
    managed_target "$target"
fi

# Copying is complete before either pointer changes; never run project programs.
if [ -n "$current" ]; then
    ln -s "$current" "$lock/previous"
    mv -f "$lock/previous" "$previous"
fi
# Stage beside the launcher so rename stays atomic if bin/ is another mount.
activation=$(mktemp -d "$prefix/bin/.$name-activate.XXXXXXXX")
ln -s "$target" "$activation/$name"
mv -f "$activation/$name" "$launcher"
release=
printf 'Active executable: %s\n' "$target"
printf 'Launcher: %s\n' "$launcher"
