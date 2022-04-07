`ccbuilder` can build and install `llvm` and `gcc`.

Installation:
```
pip install [-U] ccbuilder
```

Usage:
```
ccbuilder build [compiler] [revision]
```

`compiler` must be either `llvm` or `gcc` and `revision` either a commit hash
or a tag.

For more options check `cbuilder build -h`.
