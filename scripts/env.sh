# Source this to get catala/clerk/ninja and the project venv on PATH.
export PATH="$HOME/.opam/lks/bin:$HOME/.local/bin:$PATH"
export OPAM_SWITCH_PREFIX="$HOME/.opam/lks"
export CAML_LD_LIBRARY_PATH="$HOME/.opam/lks/lib/stublibs"
export OCAMLPATH="$HOME/.opam/lks/lib"
export CATALA_COLOR=never
export PYTHONPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src"
export PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.venv/bin/python"
