# Release Notes Agentic Workflow

[`release-notes-agent.md`](release-notes-agent.md) is a GitHub Agentic Workflow
that uses the Copilot engine to draft reStructuredText notes between two release
tags. Its prompt and module-template schema are embedded in the workflow source.
It reads repository,
pull-request, issue, and security-advisory evidence, and creates one
`[release-notes-test]` issue for human review. It never creates or publishes a
release.

When a GitHub Release already exists for the target tag, the workflow uses its
raw `What's Changed` PR list as the release scope. When drafting before a release
exists, it derives scope from the supplied tag comparison. It inspects each
in-scope PR and uses recent published notes for editorial context. Routine
maintenance is deemphasized; dependency changes are still reported under
Compatibility.

## Authentication And Permissions

The workflow requests `copilot-requests: write`; gh-aw uses the per-run Actions
token for Copilot inference, so it does not require an external AI API key,
Copilot PAT, or `copilot` environment secret. This requires an organization
Copilot subscription with centralized billing and Actions-token access to
Copilot. If inference is denied, the org's Copilot billing or permission
configuration must be verified.

The agent receives read access to repository contents, issues, pull requests,
and security advisories. A separate gh-aw safe-output job receives permission to
create one review issue. No agent step gets write access, and no release is
published.

## Manual Test

The workflow must first be merged so GitHub registers it on the default branch.
Then run `gh aw run release-notes-agent` or dispatch **Release Notes Agent** in
Actions. Supply the module name, origin release tag, release tag, and optionally
a milestone. The result appears as a reStructuredText document in one issue
prefixed `[release-notes-test]`.

Review the draft for factual accuracy, especially known issues, vulnerabilities,
and any missing milestone evidence. The generated issue is only a test artifact;
copying or publishing its contents as a release remains a human action.

## Validation

Compile the Markdown prompt after editing it:

```sh
gh aw compile release-notes-agent
```

Commit the generated `.lock.yml` with the Markdown source. A compiled workflow
proves its gh-aw configuration is valid; only a manual run can confirm
Copilot-request permission and end-to-end inference access.
