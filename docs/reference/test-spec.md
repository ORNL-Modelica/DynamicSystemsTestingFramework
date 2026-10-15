# test_spec.json

The tests a project declares and their criteria. A key that is not in these
tables is an error that names it.

## The file

```{dstf-keys} spec-root
```

## A test entry

```{dstf-keys} spec-entry
```

### How it is simulated

```{dstf-keys} spec-simulation
```

### How it is compared

```{dstf-keys} spec-comparison
```

### An override

How one variable is scored. Besides these keys an override holds the fields
of its mode, on [](comparison-modes.md).

```{dstf-keys} spec-override
```

### An extra plot

```{dstf-keys} spec-plot
```

## The criteria tree

A node of the tree is a criterion (a leaf) or a group (a combinator).

### A criterion

Besides these keys a criterion holds the fields of its mode, on
[](comparison-modes.md).

```{dstf-keys} spec-leaf
```

### Every criterion and override

```{dstf-keys} spec-every-criterion
```

### A criterion's time window

```{dstf-keys} spec-window
```

### A group

```{dstf-keys} spec-combinator
```

## Schema

An editor checks the file as it is typed when the file points at this schema:

```{dstf-schema-url} test-spec
```
