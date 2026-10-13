// OMH TUI frontend shim.
//
// Hermes' launcher runs `node <HERMES_TUI_DIR>/dist/entry.js` in place of its
// own Ink app when bare `omh` sets HERMES_TUI_DIR. This file only hands the
// terminal to the Python OMH TUI (`python -I -m omh.tui`), with the interpreter
// `omh` named in OMH_TUI_PYTHON, and returns its exit code to the launcher.
//
// The launcher's PYTHONPATH points at Hermes' source root and Hermes' own
// runtime packages. The OMH interpreter must not import those, so it starts
// isolated (-I) and the value moves to OMH_TUI_GATEWAY_PYTHONPATH, which the
// TUI hands back to the gateway child it spawns.
const { spawn } = require('node:child_process')
const { constants } = require('node:os')

const FALLBACK_EXIT = 75

const python = process.env.OMH_TUI_PYTHON
if (!python) {
  process.stderr.write('omh: OMH_TUI_PYTHON is not set; the OMH TUI is opened through `omh`.\n')
  process.exit(FALLBACK_EXIT)
}

const env = { ...process.env }
env.OMH_TUI_GATEWAY_PYTHONPATH = env.PYTHONPATH ?? ''
delete env.PYTHONPATH
delete env.NODE_OPTIONS

const child = spawn(python, ['-I', '-m', 'omh.tui'], { stdio: 'inherit', env })

// The TUI reads Ctrl+C as a key in raw mode. A SIGINT that still reaches this
// process (sent to the whole group) must not end the shim and orphan the TUI.
process.on('SIGINT', () => {})
for (const signal of ['SIGTERM', 'SIGHUP']) {
  process.on(signal, () => child.kill(signal))
}

child.on('error', () => process.exit(FALLBACK_EXIT))
child.on('exit', (code, signal) => {
  if (code !== null) process.exit(code)
  process.exit(signal ? 128 + (constants.signals[signal] ?? 0) : 1)
})
