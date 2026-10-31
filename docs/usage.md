# dicflow

Shared HDL workflows distributed as a locked Bender package.

Consumers keep Project.toml as their editable configuration and use Make as
 their command entry. Add this package to bender.dependencies and select it:

```toml
[framework]
package = "dicflow"

[bender.dependencies.dicflow]
git = "https://github.com/dashygo097/dicflow.git"
rev = "main"
```

If dependencies currently use an inline TOML table, add dicflow inside that
table instead. Do not redefine the same dependency table twice.

A consumer needs Makefile, scripts/config/bootstrap.py, setup_venv.sh,
requirements.txt, and scripts/common/check_env.sh from this repository.
Tool backends, configuration validation and standard SDC generation execute
from the package selected by Bender.lock; do not copy them into consumers.

Run make deps-update to explicitly update the dependency graph, including
this framework. Ordinary make lint/synth/formal/sim preserve the locked
revisions. Custom generators and backends remain in the consumer project.
The bootstrap uses API 1; changes to that stable starter require an explicit
migration and never overwrite project-owned files during dependency updates.

The constraints table is optional and uses the built-in SDC generator.
New stages and tool backends are configured under flow.<stage>.<backend>.
Native Bender owns RTL source ordering, target selection and dependencies.

HDL libraries version their generated native Bender.yml and keep its source
paths relative. Consumers can then use the library with plain Bender. A
library must publish generated HDL alongside its manifest when applicable.

This repository is a normal private repository, not a GitHub template.
