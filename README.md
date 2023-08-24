`ccbuilder` can build and install `llvm` and `gcc`.

--- 
Basic Usage:
```
ccbuilder build [compiler] [revision]
```

`compiler` must be either `llvm` or `gcc` and `revision` either a commit hash
or a tag.


`ccbuilder` relies on clones the GCC and LLVM git repos and can build most of their recent revisions/commits/tags, e.g., 
`ccbuilder build gcc releases/gcc-13.1.0` will build GCC 13.1.0.  
The built compilers are installed under `ccbuilder`'s compiler store (a filesystem directory). 
After building a compiler, its installation location is printed, e.g.:
```
% ccbuilder build llvm main 
/path/to/ccbuilder_compiler_store/clang-ff7d2fabe074d95cefb683d4a742eec172bd36d5
% /path/to/ccbuilder_compiler_store/clang-ff7d2fabe074d95cefb683d4a742eec172bd36d5/bin/clang -v
clang version 18.0.0 (https://github.com/llvm/llvm-project.git ff7d2fabe074d95cefb683d4a742eec172bd36d5)
...
```

`ccbuilder` stores everything under `$HOME/.cache`, the default locations can
be overriden via command line flags, e.g., `ccbuilder build llvm master --compiler-store-path /some/other/path`

To update the compiler git repos use `ccbuilder --pull`.

For more options check `ccbuilder -h`.

---
Installation:
```
pip install [-U] ccbuilder
```
