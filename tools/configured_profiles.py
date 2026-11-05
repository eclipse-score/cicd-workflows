# Copyright (c) 2026 ETAS GmbH and Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Shared platform and batch contracts for configured PR checks.

Batches inherit their profile but own target selection. A batch never quietly
inherits an all-target build from its parent. Runner setup follows the selected
platform; module Bazel configs continue to select toolchains and test wrappers.
"""

from copy import deepcopy
import re
import shlex


def expand_batches(entries, path):
    from configured_checks import NAME, fields
    if not isinstance(entries, list):
        return entries
    result = []
    for original in entries:
        if not isinstance(original, dict):
            result.append(original)
            continue
        entry = deepcopy(original)
        options = entry.get('workflow-config', {})
        if not isinstance(options, dict) or 'batches' not in options:
            result.append(entry)
            continue
        batches = options.pop('batches')
        if not isinstance(batches, list) or not batches:
            raise ValueError(f'{path}.batches: expected a nonempty list')
        names = set()
        for batch in batches:
            fields(batch, {'batch-name', 'bazel-build-targets', 'bazel-test-targets'},
                   {'batch-name', 'bazel-test-targets'}, f'{path}.batches')
            name = batch['batch-name']
            if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
                raise ValueError(f'{path}.batches: invalid or duplicate batch name')
            names.add(name)
            expanded = {k: deepcopy(v) for k, v in entry.items()
                        if k not in ('bazel-build-targets', 'bazel-test-targets')}
            expanded.update({k: v for k, v in batch.items() if k != 'batch-name'})
            expanded['name'] = f'{entry.get("name", entry.get("test-type", "unit"))}-{name}'
            result.append(expanded)
    return result


def test_arguments(value, path):
    """Preserve test controls as argv; never evaluate the old shell command text."""
    if not isinstance(value, str) or '\n' in value or '\r' in value:
        raise ValueError(f'{path}: test CLI arguments must be a single-line string')
    result = shlex.split(value)
    patterns = [
        r'--(?:test|build)_tag_filters=[A-Za-z0-9_,+.-]+',
        r'--test_output=(?:errors|all|summary|streamed)',
        r'--(?:no)?cache_test_results(?:=(?:yes|no|true|false|auto))?',
        r'--(?:no)?build_tests_only',
        r'--(?:runs_per_test|flaky_test_attempts)=[1-9][0-9]*',
        r'--test_timeout=[1-9][0-9]*(?:,[1-9][0-9]*){0,3}',
        r'--test_arg=[^\x00-\x1f]+',
    ]
    if any(not any(re.fullmatch(pattern, arg) for pattern in patterns) for arg in result):
        raise ValueError(f'{path}: unsupported test/tag-filter argument')
    return result


def platform_profile(platform, family, options):
    """QNX runs system VMs; Linux ARM tests explicitly opt into user emulation."""
    os_name, arch = platform['os'], platform['architecture']
    if os_name not in ('linux', 'qnx') or arch not in ('x86_64', 'aarch64'):
        raise ValueError('Supported platforms: Linux and QNX, x86_64 and aarch64')
    default = 'qnx-vm' if os_name == 'qnx' and family == 'tests' else 'native'
    execution = options.get('execution', default)
    if execution not in ('native', 'qemu-user', 'qnx-vm'):
        raise ValueError('execution must be native, qemu-user or qnx-vm')
    if execution == 'qemu-user' and (os_name != 'linux' or arch != 'aarch64' or family != 'tests'):
        raise ValueError('qemu-user requires Linux aarch64 tests')
    if execution == 'qnx-vm' and (os_name != 'qnx' or family != 'tests'):
        raise ValueError('qnx-vm requires QNX tests')
    if os_name == 'qnx' and family == 'tests' and execution != 'qnx-vm':
        raise ValueError('QNX tests require qnx-vm execution')
    return execution
