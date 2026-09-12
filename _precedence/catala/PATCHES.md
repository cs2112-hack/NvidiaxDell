# This is a patched Catala

Vendored from https://github.com/CatalaLang/catala at `b7623302`
(`nightly-137-gb7623302`), plus seven fixes for defects found while building
the legal ingestion pipeline. The delta is recorded in `../catala-fixes.patch`
(437 lines, 15 files).

Do not treat this tree as pristine upstream. Upstream's own suite passes on it:
`tests/` 707/707 and `tests-extra/proof` 66/66.

## What was fixed

| # | Defect on stock nightly-137 | Fix |
|---|---|---|
| 1 | Any fence label other than ```` ```catala ````/```` ```catala-metadata ```` — including ```` ```catala_en ```` and any capitalisation — was silently treated as prose, so a whole statute could vanish while every gate reported success | `compiler/surface/lexer.cppo.ml` now warns, naming the label |
| 2 | `clerk test --code-coverage --verbose` divided reached by *unreached* lines: `400 %` for 8/10, `-1 %` at full coverage | `build_system/clerk_report.ml` divides by reachable lines, guards zero |
| 3 | Include cycles recursed until file descriptors ran out (`Too many open files`) | guards in `parser_driver.ml`, `clerk_utils/scan.ml`, `clerk_rules.ml` |
| 4 | Generated Python `__init__.py` emitted `__all__ = [Mod]` — bare identifiers, so any generated package raised `NameError` | `clerk_backend/python.ml` quotes them |
| 5 | Generated modules did `from . import Stdlib_en` though the stdlib installs as the separate `libcatala` package | `compiler/scalc/to_python.ml` imports from `libcatala` |
| 6 | `List.sequence`'s docstring said `sequence of 3, 6 = [3;4;5;6]` while the implementation is half-open — an off-by-one in every period-based computation | `stdlib/list_en.catala_en` docstring corrected |
| 7 | `catala proof` exits 0 even when it proves a defect, so it cannot gate CI | opt-in `--fail-on-unproven` (default unchanged) |

Fixes 4 and 5 together mean generated Python is importable at all for the first
time; with them, it agrees with the interpreter to the cent across compound
interest, division and rounding.

## Building

Needs `opam`, `ninja`, `pkgconf`. A local switch keeps everything inside this
directory:

```sh
opam switch create . ocaml-base-compiler.5.3.0 --no-install
opam install ./catala.opam --deps-only --switch "$PWD" --confirm-level=unsafe-yes
opam exec --switch "$PWD" -- dune build catala.install --promote-install-files
opam install ./catala.opam --working-dir --assume-built --switch "$PWD"
export PATH="$PWD/_opam/bin:$PATH"
```

For `catala proof` (static conflict and gap detection, with counterexamples):

```sh
opam install z3 --switch "$PWD" --confirm-level=unsafe-yes
opam exec --switch "$PWD" -- dune build compiler/plugins/ catala-proof.install --promote-install-files
opam install ./catala-proof.opam --working-dir --assume-built --switch "$PWD"
```

`make compiler` fails at the end on `Library "z3" not found` if z3 is absent;
`catala.exe` and `clerk.exe` are already built by then.
