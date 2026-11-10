// Copyright (c) 2026 Contributors to the Eclipse Foundation
// SPDX-License-Identifier: Apache-2.0
// Keep credentials outside the checkout and expose only their path to later steps.
const fs = require('node:fs');
const path = require('node:path');

const content = process.env.INPUT_CONTENT;
if (content) {
  const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'bazel-netrc-'));
  try {
    // Save cleanup state first, including when a later setup operation fails.
    fs.appendFileSync(process.env.GITHUB_STATE, `netrcDirectory=${directory}\n`);
    const filename = path.join(directory, '.netrc');
    fs.writeFileSync(filename, content, { mode: 0o600, flag: 'wx' });
    // Bazel's repository download rules use NETRC ahead of the home .netrc.
    fs.appendFileSync(process.env.GITHUB_ENV, `NETRC=${filename}\n`);
  } catch (error) {
    fs.rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}
