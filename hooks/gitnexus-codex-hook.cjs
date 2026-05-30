#!/usr/bin/env node
/**
 * Codex compatibility wrapper for the GitNexus Claude hook.
 *
 * Codex validates hook stdout against event-specific schemas. This wrapper keeps
 * the Codex entrypoint stable across GitNexus upgrades by normalizing GitNexus
 * hook output and swallowing non-JSON diagnostics that would otherwise break
 * PreToolUse/PostToolUse parsing.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const HOME = process.env.HOME || os.homedir();
const LOG_DIR = path.join(HOME, '.codex', 'hooks', 'logs');
const LOG_PATH = path.join(LOG_DIR, 'gitnexus-codex-hook.log');
const MAX_LOG_BYTES = 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 7600;

const ALLOWED_TOP_LEVEL = new Set([
  'continue',
  'decision',
  'hookSpecificOutput',
  'reason',
  'stopReason',
  'suppressOutput',
  'systemMessage',
]);

const ALLOWED_HOOK_OUTPUT = new Set([
  'additionalContext',
  'hookEventName',
  'permissionDecision',
  'permissionDecisionReason',
  'updatedInput',
  'updatedMCPToolOutput',
]);

function readInput() {
  try {
    return fs.readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

function parseInput(rawInput) {
  try {
    return JSON.parse(rawInput || '{}');
  } catch {
    return {};
  }
}

function logDebug(message, details) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    if (fs.existsSync(LOG_PATH) && fs.statSync(LOG_PATH).size > MAX_LOG_BYTES) {
      fs.renameSync(LOG_PATH, `${LOG_PATH}.1`);
    }
    const payload = details ? ` ${JSON.stringify(details).slice(0, 4000)}` : '';
    fs.appendFileSync(LOG_PATH, `${new Date().toISOString()} ${message}${payload}\n`);
  } catch {
    /* logging must never break hook execution */
  }
}

function findOriginalHook() {
  const candidates = [
    process.env.GITNEXUS_CODEX_ORIGINAL_HOOK,
    path.join(HOME, '.claude', 'hooks', 'gitnexus', 'gitnexus-hook.cjs'),
    '/opt/homebrew/lib/node_modules/gitnexus/hooks/claude/gitnexus-hook.cjs',
    '/usr/local/lib/node_modules/gitnexus/hooks/claude/gitnexus-hook.cjs',
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function parseJsonFromStdout(stdout) {
  const trimmed = String(stdout || '').trim();
  if (!trimmed) return null;

  try {
    return JSON.parse(trimmed);
  } catch {
    for (const line of trimmed.split(/\r?\n/).reverse()) {
      const candidate = line.trim();
      if (!candidate.startsWith('{') || !candidate.endsWith('}')) continue;
      try {
        return JSON.parse(candidate);
      } catch {
        /* try earlier lines */
      }
    }
  }
  return null;
}

function sanitizeHookSpecificOutput(rawOutput, eventName) {
  const source = rawOutput && typeof rawOutput === 'object' ? rawOutput : {};
  const output = {};

  for (const [key, value] of Object.entries(source)) {
    if (ALLOWED_HOOK_OUTPUT.has(key) && value !== undefined && value !== null) {
      output[key] = value;
    }
  }

  if (typeof output.hookEventName !== 'string' || !output.hookEventName.trim()) {
    output.hookEventName = eventName || 'PreToolUse';
  }

  return output;
}

function normalizeOutput(stdout, eventName) {
  const parsed = parseJsonFromStdout(stdout);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

  const normalized = {};
  for (const [key, value] of Object.entries(parsed)) {
    if (ALLOWED_TOP_LEVEL.has(key) && value !== undefined && value !== null) {
      normalized[key] = value;
    }
  }

  if (parsed.hookSpecificOutput && typeof parsed.hookSpecificOutput === 'object') {
    normalized.hookSpecificOutput = sanitizeHookSpecificOutput(
      parsed.hookSpecificOutput,
      eventName,
    );
  } else if (typeof parsed.additionalContext === 'string' && parsed.additionalContext) {
    normalized.hookSpecificOutput = {
      hookEventName: eventName || 'PreToolUse',
      additionalContext: parsed.additionalContext,
    };
  }

  if (!normalized.hookSpecificOutput && !normalized.decision && normalized.continue === undefined) {
    return null;
  }
  return normalized;
}

function runOriginalHook(rawInput, originalHook) {
  return spawnSync(process.execPath, [originalHook], {
    input: rawInput,
    encoding: 'utf8',
    timeout: DEFAULT_TIMEOUT_MS,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

function runHook() {
  const rawInput = readInput();
  const input = parseInput(rawInput);
  const eventName = typeof input.hook_event_name === 'string' ? input.hook_event_name : '';
  const originalHook = findOriginalHook();

  if (!originalHook) {
    logDebug('original hook not found');
    return 0;
  }

  let result;
  try {
    result = runOriginalHook(rawInput, originalHook);
  } catch (error) {
    logDebug('original hook threw', { message: error && error.message });
    return 0;
  }

  if (result.error || result.status !== 0 || result.signal) {
    logDebug('original hook failed', {
      error: result.error && result.error.message,
      status: result.status,
      signal: result.signal,
      stderr: String(result.stderr || '').slice(0, 1000),
    });
    return 0;
  }

  const normalized = normalizeOutput(result.stdout, eventName);
  if (!normalized) {
    if ((result.stdout || '').trim() || (result.stderr || '').trim()) {
      logDebug('hook output suppressed', {
        stdout: String(result.stdout || '').slice(0, 1000),
        stderr: String(result.stderr || '').slice(0, 1000),
      });
    }
    return 0;
  }

  process.stdout.write(`${JSON.stringify(normalized)}\n`);
  if ((result.stderr || '').trim()) {
    logDebug('hook stderr suppressed', { stderr: String(result.stderr).slice(0, 1000) });
  }
  return 0;
}

function runWrapperChild(originalHook, input) {
  return spawnSync(process.execPath, [__filename], {
    input: JSON.stringify(input),
    encoding: 'utf8',
    timeout: DEFAULT_TIMEOUT_MS,
    env: {
      ...process.env,
      GITNEXUS_CODEX_ORIGINAL_HOOK: originalHook,
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

function assertSelfTest(condition, message) {
  if (!condition) throw new Error(message);
}

function runSelfTest() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gitnexus-codex-hook-'));
  const fakeHook = path.join(tempDir, 'fake-gitnexus-hook.cjs');

  fs.writeFileSync(
    fakeHook,
    `#!/usr/bin/env node
const scenario = process.env.GITNEXUS_CODEX_SELFTEST_SCENARIO || '';
if (scenario === 'top-level') {
  process.stderr.write('non-json diagnostic\\\\n');
  console.log(JSON.stringify({ additionalContext: 'graph context' }));
} else if (scenario === 'nested') {
  console.log(JSON.stringify({ hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: 'stale index' } }));
} else if (scenario === 'invalid') {
  process.stderr.write('[GitNexus] augment skipped: MCP server owns DB\\\\n');
} else {
  process.exit(2);
}
`,
  );
  fs.chmodSync(fakeHook, 0o755);

  try {
    const baseInput = {
      cwd: process.cwd(),
      hook_event_name: 'PreToolUse',
      tool_input: { command: 'rg symbol .' },
      tool_name: 'Bash',
    };

    process.env.GITNEXUS_CODEX_SELFTEST_SCENARIO = 'top-level';
    const topLevel = runWrapperChild(fakeHook, baseInput);
    assertSelfTest(topLevel.status === 0, 'top-level scenario exited non-zero');
    assertSelfTest(!topLevel.stderr.trim(), 'top-level scenario leaked stderr');
    const topLevelOutput = JSON.parse(topLevel.stdout);
    assertSelfTest(
      topLevelOutput.hookSpecificOutput.hookEventName === 'PreToolUse',
      'top-level scenario did not set PreToolUse hookEventName',
    );
    assertSelfTest(
      topLevelOutput.hookSpecificOutput.additionalContext === 'graph context',
      'top-level scenario did not preserve context',
    );

    process.env.GITNEXUS_CODEX_SELFTEST_SCENARIO = 'nested';
    const nested = runWrapperChild(fakeHook, { ...baseInput, hook_event_name: 'PostToolUse' });
    assertSelfTest(nested.status === 0, 'nested scenario exited non-zero');
    const nestedOutput = JSON.parse(nested.stdout);
    assertSelfTest(
      nestedOutput.hookSpecificOutput.hookEventName === 'PostToolUse',
      'nested scenario did not preserve PostToolUse hookEventName',
    );

    process.env.GITNEXUS_CODEX_SELFTEST_SCENARIO = 'invalid';
    const invalid = runWrapperChild(fakeHook, baseInput);
    assertSelfTest(invalid.status === 0, 'invalid scenario exited non-zero');
    assertSelfTest(!invalid.stdout.trim(), 'invalid scenario leaked stdout');
    assertSelfTest(!invalid.stderr.trim(), 'invalid scenario leaked stderr');

    process.stdout.write('PASS gitnexus-codex-hook self-test\n');
    return 0;
  } finally {
    delete process.env.GITNEXUS_CODEX_SELFTEST_SCENARIO;
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

try {
  if (process.argv.includes('--self-test')) {
    process.exit(runSelfTest());
  }
  process.exit(runHook());
} catch (error) {
  logDebug('wrapper failed', { message: error && error.message });
  if (process.argv.includes('--self-test')) {
    process.stderr.write(`${error && error.message ? error.message : error}\n`);
    process.exit(1);
  }
  process.exit(0);
}
