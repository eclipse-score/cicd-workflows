# Daily Maintenance Workflow (`daily.yml`)

`daily.yml` groups the repository's recurring maintenance tasks. It handles
stale pull requests and cleans up old documentation when GitHub Pages is
enabled for the repository. It also prunes obsolete GitHub Actions caches.

## Usage

Create `.github/workflows/daily.yml` in the consuming repository:

```yaml
name: Daily Maintenance

on:
  schedule:
    - cron: '0 3 * * *' # Daily at 3am UTC
  workflow_dispatch:

permissions:
  contents: write
  issues: write
  pull-requests: write
  pages: write
  id-token: write
  actions: write

jobs:
  daily:
    uses: eclipse-score/cicd-workflows/.github/workflows/daily.yml@main
```

The reusable workflow does not expose any parameters. It applies a fixed
maintenance policy:

- Marks pull requests as stale after `30` days of inactivity using the `stale` label
- Closes stale pull requests after `10` more days, adding the `autoclosed` label when closing
- Exempts pull requests labeled `keep-open`, `do-not-close`, `pinned`, or `feature_request`
- Runs documentation cleanup on the `gh-pages` branch when GitHub Pages is enabled
- Prunes obsolete GitHub Actions caches
