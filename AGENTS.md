# Project-KISS Development Rules

Treat this directory as the project root and apply the following principles to all work.

1. Give top priority to KISS (Keep It Simple, Stupid).
2. Do not decide specifications or configuration based on assumptions.
3. Investigate anything unclear, and ask if it cannot be verified.
4. Prioritize official documentation and observed behavior.
5. Do not break the host's existing environment.
6. Do not add packages to the host, change drivers, or make BIOS changes. If any of these actions is necessary, obtain the user's explicit approval before proceeding.
7. Use Docker by default, and do not use privileged mode.
8. Do not expose APIs or services externally.
9. Do not introduce unnecessary abstractions, dependencies, or configuration options.
10. Always verify after making changes, and report the results and anything left unverified.
11. Do not delete or overwrite existing files without permission.
12. Do not push to GitHub until explicit permission has been given.
13. Do not track model data, credentials, caches, or logs in Git.
14. Proceed in this order: investigation, planning, implementation, verification.
15. Do not start implementation before the plan is approved.
