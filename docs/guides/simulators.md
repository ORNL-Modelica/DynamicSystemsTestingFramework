# Set up a simulator

dstf drives a simulator through a backend. Five ship with it; a plug-in can
add more ([Write a backend plug-in](plugin.md)). Each page below says what to
install, what `dstf check` prints when the simulator is found and when it is
not, and where the backend's settings are.

```{toctree}
:maxdepth: 1

simulators/dymola
simulators/openmodelica
simulators/fmpy
simulators/julia
simulators/python
```

`dstf check` works anywhere, with or without a project. With no backend named
it lists every backend and where each was found; given one, it diagnoses that
one in full and exits 0 when it is usable:

```console
$ dstf check OpenModelica
```

A backend is found by the first of these rules that gives an answer: the
command line's `--backend-path`, the `executables` of its block in
`testing.json`, an environment variable where the backend has one, then the
search — `PATH` and the simulator's usual install folders.
