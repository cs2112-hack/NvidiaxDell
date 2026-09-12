#!/usr/bin/env bash
# Install the Catala compiler into a dedicated opam switch.
# Tries the system OCaml first (fast); falls back to building a compiler if
# Catala's constraints require a newer one.
set -x
export OPAMYES=1 OPAMCOLOR=never

opam init --bare -y --disable-sandboxing || exit 10
eval "$(opam env --switch=default --set-switch 2>/dev/null)"
opam update -y

if ! opam switch list --short | grep -qx lks; then
  opam switch create lks ocaml-system || opam switch create lks 4.14.2 || exit 11
fi
eval "$(opam env --switch=lks --set-switch)"
ocamlc -version

# Catala pulls a large dependency tree (zarith, dates_calc, ninja_utils, ...).
if opam install -y catala; then
  echo "CATALA_INSTALL=ok-system-switch"
else
  echo "system switch failed; building a 5.x compiler"
  opam switch remove -y lks || true
  opam switch create lks 5.1.1 || exit 12
  eval "$(opam env --switch=lks --set-switch)"
  opam install -y catala || exit 13
  echo "CATALA_INSTALL=ok-5.1.1"
fi

eval "$(opam env --switch=lks --set-switch)"
which catala
catala --version
echo "DONE"
