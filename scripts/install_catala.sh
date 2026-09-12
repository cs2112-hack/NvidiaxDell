#!/usr/bin/env bash
# Install the Catala compiler into the `lks` opam switch.
# ninja is provided rootlessly from the project venv (see ~/.local/bin/ninja),
# so opam's depext check is skipped with --assume-depexts.
set -x
export OPAMYES=1 OPAMCOLOR=never
export PATH="$HOME/.local/bin:$PATH"

eval "$(opam env --switch=lks --set-switch)" || exit 10
ocamlc -version
which ninja && ninja --version

opam install -y --assume-depexts catala.1.2.1 || \
  opam install -y --assume-depexts catala || exit 13

eval "$(opam env --switch=lks --set-switch)"
which catala clerk
catala --version
clerk --version
echo "DONE_OK"
