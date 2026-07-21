# Assistant Catalog

This directory is the canonical source for platform-published assistant profiles and their entry Skills.

- `clinical-assistant/skills/` contains the small entry Skills copied into each user workspace.
- The repository-level `.opencode/skills/` files are local development mirrors and must remain byte-for-byte identical.
- The Murtagh knowledge corpus is stored separately under `.opencode/skills/disease-guidelines/skill/` and is served through MCP; it is never copied into a user workspace.
- Update the assistant version when changing behavior that external deployments need to track.

Run `pytest tests/test_assistant_installer.py` after changing an assistant package. The tests validate the mirror files and packaged payload.
