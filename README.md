# Honey

Honey is currently bootstrapped with the Bro compiler. The expected development workspace places the Bro project at [`../brolang`](../brolang); consult that project for compiler behavior and documentation.

Build Honey from this directory:

```sh
bro build
```

The parser supports arithmetic expressions with nested parenthesized groups, such as `a + (b - c) * 2 / 3`. Groups control precedence without adding AST nodes; inner expression spans exclude the grouping parentheses.
