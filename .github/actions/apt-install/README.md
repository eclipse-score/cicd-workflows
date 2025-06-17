# Installing packages... the convinient way

## How to use

Add the following step to your GitHub Actions workflow to install packages via `apt`:

```yaml
    - name: Install dependencies
      uses: eclipse-score/cicd-workflows/.github/actions/apt-install@main
      with:
        packages: dia doxygen doxygen-doc doxygen-gui doxygen-latex graphviz mscgen
```

## What does it do?

This action combines some typical steps:

* Disables `mandb` updates to speed up `apt` installs
* Updates the `apt` package list
* Installs the specified packages via `apt`, excluding recommended packages

## Measurements

### mandb

Installing graphviz 114 times on Ubuntu 24.04 with different methods, with and without mandb updates.

| Method | Mean (s) | Median (s) | 30–60s | >60s |
|--------|----------|------------|--------|------|
|*naive: with mandb update*| | | | |
| Download and install .deb files | 🔴 27.7 | 🟡 18.0 | 4 | 18 |
| Get .deb files from GitHub cache and install them | 🔴 21.2 | 🟡 14.0 | 5 | 10 |
| Install via apt | 🔴 29.2 | 🟡 20.0 | 0 | 18 |
| Install via apt, no apt update | 🔴 25.1 | 🟡 14.0 | 11 | 15 |
| Install via apt, no apt update, no recommends | 🔴 22.7 | 🟡 14.0 | 6 | 13 |
|*without mandb update*| | | | |
| Download and install .deb files, no mandb | 🟡 10.5 | 🟢 10.0 | 0 | 0 |
| Get .deb files from GitHub cache and install them, no mandb | 🟢 8.1 | 🟢 7.0 | 0 | 0 |
| Install via apt, no mandb | 🟡 14.3 | 🟡 13.0 | 3 | 0 |
| Install via apt, no apt update, no mandb | 🟢 7.7 | 🟢 7.0 | 0 | 0 |
| Install via apt, no apt update, no recommends, no mandb | 🟢 8.1 | 🟢 6.0 | 0 | 0 |

Disabling mandb updates greatly reduces the >30 and >60 seconds outliers.

### apt update

Installing graphviz 114 times on Ubuntu 24.04 with different methods, with and without apt update.

| Method | Mean (s) | Median (s) | 30–60s | >60s |
|--------|----------|------------|--------|------|
| Install via apt, no mandb | 🟡 14.3 | 🟡 13.0 | 3 | 0 |
| Install via apt, no apt update, no mandb | 🟢 7.7 | 🟢 7.0 | 0 | 0 |

### recommends

Installing graphviz 114 times on Ubuntu 24.04 with different methods, with and without recommended dependencies.

| Method | Mean (s) | Median (s) | 30–60s | >60s |
|--------|----------|------------|--------|------|
| Install via apt, no apt update, no mandb | 🟢 7.7 | 🟢 7.0 | 0 | 0 |
| Install via apt, no apt update, no recommends, no mandb | 🟢 8.1 | 🟢 6.0 | 0 | 0 |

*It's not quite obvious from this data, but skipping recommended dependencies should obviously speed up the installation, as it installs fewer packages.*

## Comparison with similar actions

*No action identified that supports disabling `mandb` updates.*

