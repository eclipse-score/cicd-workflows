// Copyright (c) 2026 Contributors to the Eclipse Foundation
// SPDX-License-Identifier: Apache-2.0
// GitHub runs this after later actions' post steps, even when the job fails.
const fs = require('node:fs');

const directory = process.env.STATE_netrcDirectory;
if (directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}
