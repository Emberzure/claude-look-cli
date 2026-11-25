import type { Register } from 'claude-code'

const PANE = 'xiaoke-frame'
// python3 from PATH: /usr/bin/python3 does not exist on NixOS, macOS, or many
// containers; process.run's env sits over the host's, so PATH is inherited.
const PYTHON = 'python3'

type Helper = { ok: boolean; out: string }

// Widths: the dock's effective body columns, the width we last asked for, the
// width the person dragged (0 = never), and the last one written to disk.
let dockColumns = 0
let requestedColumns = 0
let dragColumns = 0
let persistedDrag = 0
let placeFailed = false
let refreshBusy = false
let workHome = ''
let workCwd = ''
let workGit = ''
let workUsage = ''
let workModel = ''

// The surface's size, as the last terminal draw reported it, and whether the
// frame was on before the window narrowed.
let viewColumns = 0
let viewFullscreen = true
let viewWide: boolean | null = null
let viewCanOpen: boolean | null = null
let frameOn = false
let everOpened = false
let panePlacement = ''
let paneOpen = false
let lastStatus = ''
let pendingKey: string | null = null
let pendingSince = 0
let lastDockTry = 0
let chain: Promise<unknown> = Promise.resolve()

function run($: any, args: string[]): Promise<Helper> {
  return (async () => {
    try {
      const env = await $.process.hookEnv()
      const root = env.CLAUDE_PLUGIN_ROOT
      if (!root) {
        return { ok: false, out: 'CLAUDE_PLUGIN_ROOT is missing' }
      }
      const res = await $.process.run(
        [PYTHON, '-I', `${root}/bin/xiaoke.py`, ...args],
        { env, timeoutMs: 20000 },
      )
      return { ok: res.exitCode === 0, out: `${res.stdout}${res.stderr}`.trim() }
    } catch (error) {
      return { ok: false, out: String(error) }
    }
  })()
}

async function read($: any, key: string): Promise<string> {
  const res = await run($, ['get', key])
  if (!res.ok) {
    throw new Error(res.out || `xiaoke get ${key} failed`)
  }
  return res.out.trim()
}

async function isOpen($: any): Promise<boolean> {
  try {
    return (await $.ui.panes()).some((pane: { id: string }) => pane.id === PANE)
  } catch {
    return false
  }
}

// Serialize open/close so a timer's open cannot land after the person's close.
function enqueue<T>(task: () => Promise<T>): Promise<T> {
  const next = chain.then(task, task)
  chain = next.then(() => undefined, () => undefined)
  return next
}

function openWidth(): number | undefined {
  if (dragColumns > 0) return dragColumns
  // The pane is closed here, so the viewport is the terminal's own width.
  return viewColumns > 0 ? Math.max(40, Math.floor(viewColumns * 0.44)) : undefined
}

function shortPath(path: string): string {
  let p = path || '…'
  if (workHome && p.startsWith(workHome)) p = `~${p.slice(workHome.length)}`
  if (p.length <= 36) return p
  return `…/${p.split('/').filter(Boolean).slice(-2).join('/')}`
}

// cwd and git status for the status line, refreshed on a timer.
async function refreshWork($: any): Promise<void> {
  if (refreshBusy) return
  refreshBusy = true
  try {
    await refreshWorkBody($)
    // Recover a pane the engine dropped (a /clear that closed it) without
    // waiting for a resize; the pane is shut here, so the viewport is the
    // terminal's own width.
    if (frameOn) {
      paneOpen = await isOpen($)
      if (!paneOpen && viewFullscreen && viewColumns >= 110) {
        await showPane($, openWidth(), false)
      }
    }
  } finally {
    refreshBusy = false
  }
}

async function refreshWorkBody($: any): Promise<void> {
  try {
    workCwd = await $.session.cwd()
  } catch {
    // keep the last one
  }
  try {
    const res = await $.process.run(
      ['git', 'status', '--porcelain=v1', '--branch', '--untracked-files=normal'],
      { cwd: workCwd, timeoutMs: 8000 },
    )
    if (res.exitCode !== 0) {
      workGit = 'no git'
    } else {
      const lines = `${res.stdout}`.split('\n')
      const head = lines.find((line: string) => line.startsWith('## ')) ?? '## ?'
      let branch = head.slice(3).trim()
      if (branch.startsWith('No commits yet on ')) {
        branch = branch.slice('No commits yet on '.length)
      } else {
        const beforeDots = branch.split('...')[0] ?? ''
        branch = (beforeDots.split(' [')[0] ?? '').trim()
      }
      if (!branch) branch = 'detached'
      const changed = lines.filter((line: string) => line && !line.startsWith('##'))
      const fresh = changed.filter((line: string) => line.startsWith('??')).length
      const dirty = changed.length - fresh
      workGit = dirty === 0 && fresh === 0
        ? `${branch} · clean`
        : `${branch} · ${dirty} changed${fresh ? ` · ${fresh} new` : ''}`
    }
  } catch {
    workGit = 'no git'
  }
  try {
    const usage = await $.session.usage()
    const percent = usage.context.percent
    workUsage = percent === undefined ? '--%' : `${Math.round(percent)}%`
  } catch {
    workUsage = '--%'
  }
  try {
    workModel = await $.session.model()
  } catch {
    workModel = 'model?'
  }
  const parts = [shortPath(workCwd), workGit, `${workModel || '…'} · ${workUsage || '--%'}`]
  const line = parts.filter(Boolean).join(' · ')
  if (line !== lastStatus) {
    lastStatus = line
    try {
      $.ui.status(line)
    } catch {
      // no status line on this surface
    }
  }
}

async function showPaneNow($: any, columns?: number, asked = false): Promise<Helper> {
  if (!frameOn) {
    return { ok: false, out: 'frame off' }
  }
  requestedColumns = columns ?? 0
  let opened: { isPlaced: boolean; reason?: string }
  try {
    opened = await $.ui.open({
      id: PANE,
      title: '小克',
      ...(columns ? { columns } : {}),
      // Paint nothing under the body: the kitty background image shows through
      // the reserved cells instead of the theme's opaque surface color.
      backgroundColor: 'transparent',
    })
  } catch (error) {
    await run($, ['bg', 'off'])
    return { ok: false, out: `cannot open pane: ${String(error)}` }
  }
  if (!opened.isPlaced) {
    // Leave no waiting pane behind: if the engine placed it later, nothing
    // would paint its background.
    placeFailed = true
    paneOpen = false
    try {
      await $.ui.close({ id: PANE })
    } catch {
      // already gone
    }
    await run($, ['bg', 'off'])
    return { ok: false, out: `pane waits: ${opened.reason ?? 'not placed'}` }
  }
  placeFailed = false
  if (asked) everOpened = true
  paneOpen = true
  const painted = await run($, ['bg', 'on', 'dock'])
  if (!painted.ok) {
    // No kitty background (another terminal, or remote control off): close
    // the pane rather than leave it reserving half the screen, empty.
    try {
      await $.ui.close({ id: PANE })
    } catch {
      // already gone
    }
    paneOpen = false
    return painted
  }
  return painted
}

function showPane($: any, columns?: number, asked = false): Promise<Helper> {
  return enqueue(() => showPaneNow($, columns, asked))
}

async function hidePaneNow($: any): Promise<void> {
  paneOpen = false
  try {
    await $.ui.close({ id: PANE })
  } catch {
    // another plugin's pane, or already gone: nothing to close
  }
  await run($, ['bg', 'off'])
}

function hidePane($: any): Promise<void> {
  return enqueue(() => hidePaneNow($))
}

// The pane keeps the transcript on its side of the screen, so a scene change
// must leave it up: reopened when it went missing, retitled and re-painted
// when it is there.
async function ensureFrame($: any, e: any): Promise<void> {
  try {
    frameOn = (await read($, 'frame')) === 'on'
  } catch {
    frameOn = false
  }
  if (!frameOn) return
  if (!e.presentation.isFullscreen || e.presentation.columns < 110) {
    await run($, ['bg', 'off'])
    return
  }
  everOpened = true
  // Up to 44%: the API caps a dock at 45%, and the wider it is, the closer the
  // pane lands over the illustration's character on wide screens.
  const dockCols = dragColumns > 0
    ? dragColumns
    : Math.max(40, Math.floor(e.presentation.columns * 0.44))
  await showPane($, dockCols, true)
}

async function restoreFrame($: any): Promise<void> {
  try {
    frameOn = (await read($, 'frame')) === 'on'
  } catch {
    frameOn = false
  }
  if (!frameOn) {
    // A background image left over from an earlier session must not sit
    // behind text before a pane reserves room for it.
    await run($, ['bg', 'off'])
    return
  }
  // A saved frame-on preference means the person opened the pane before.
  everOpened = true
  // The width the person dragged last time, so a new session or a reload
  // opens the dock exactly as they left it.
  try {
    const stored = Number((await read($, 'dock_cols')) || 0)
    if (stored > 0) {
      dragColumns = stored
      persistedDrag = stored
    }
  } catch {
    await run($, ['bg', 'off'])
    return
  }
  await showPane($, dragColumns > 0 ? dragColumns : undefined, false)
}

async function tick($: any): Promise<void> {
  await run($, ['tick', '--quiet'])
  try {
    frameOn = (await read($, 'frame')) === 'on'
  } catch {
    return
  }
  paneOpen = await isOpen($)
  if (frameOn && !paneOpen && viewFullscreen && viewColumns >= 110) {
    await showPane($, openWidth(), false)
  }
}

// The dock needs 110 columns beside a fullscreen transcript. Below that (a
// half-screen window) the frame is hidden, keeping the person's frame-on
// preference; widening opens it again only when that preference is on.
async function syncWide($: any): Promise<void> {
  // Persist a drag as soon as it is seen: an unload close never reaches the
  // opener's hooks.
  if (dragColumns > 0 && dragColumns !== persistedDrag) {
    // Retry on a failure at most every 5 s, not on every 250 ms poll.
    const now = Date.now()
    if (now - lastDockTry > 5_000) {
      lastDockTry = now
      const saved = await run($, ['dock', String(dragColumns)])
      if (saved.ok) persistedDrag = dragColumns
    }
  }
  if (viewColumns === 0) return
  const docked = paneOpen && panePlacement !== 'inline' && panePlacement !== 'widget'
  // The render viewport of a transcript site is measured beside a docked
  // pane; add the dock back to read the terminal's real width.
  const terminal = viewColumns + (docked ? dockColumns + 2 : 0)
  const wide = terminal >= 110 && viewFullscreen
  // An unasked open waits below 144 columns; one the person opened before
  // waits only below 110. After a refusal, wait for 144.
  const floor = placeFailed ? 144 : (everOpened ? 110 : 144)
  const canOpen = wide && terminal >= floor
  if (wide === viewWide && canOpen === viewCanOpen) {
    pendingKey = null
    return
  }
  // Wait until the width has held still, so dragging across the threshold
  // does not open and close the pane mid-drag.
  const key = `${wide}:${canOpen}`
  const now = Date.now()
  if (pendingKey !== key) {
    pendingKey = key
    pendingSince = now
    return
  }
  if (now - pendingSince < 250) return
  pendingKey = null
  viewWide = wide
  viewCanOpen = canOpen
  if (!wide) {
    if (paneOpen) {
      await hidePane($)
    } else {
      await run($, ['bg', 'off'])
    }
    return
  }
  if (canOpen && frameOn && !paneOpen) {
    await showPane($, openWidth(), false)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'xiaoke',
      description: '小克 look：fullscreen 下在右侧停靠插画 / 切换场景 / 自动排期',
    })
    await run($, ['session-start'])
    // Bring the frame back when the person left it on. A reload (the plugin
    // folder is watched) lands here too, so this is also what survives one.
    if (e.isInteractive && e.surface === 'terminal') {
      // hookEnv carries only the session/plugin names, not HOME.
      try {
        workHome = (await $.env.get('HOME')) ?? ''
      } catch {
        workHome = ''
      }
      await restoreFrame($)
      void refreshWork($).catch(() => {})
      $.clock.every(30_000, () => { void refreshWork($).catch(() => {}) })
      $.clock.every(250, () => { void syncWide($).catch(() => {}) })
    }
    $.clock.every(600_000, () => { void tick($).catch(() => {}) })
    return next(e)
  })

  // The context fill and the model can change during a turn: refresh the card
  // as each one ends, so the percentage is not up to a timer behind.
  on('turn.complete', async ($, e, next) => {
    void refreshWork($).catch(() => {})
    return next(e)
  }).catch(($, e, next) => next.called ? next(e) : undefined)

  on('session.end', async ($, e, next) => {
    // /clear ends the conversation but keeps the process and the pane, and
    // fires no session.start after it: clearing here would leave a blank dock.
    if (e.reason === 'clear') {
      return next(e)
    }
    try {
      $.ui.status(undefined)
    } catch {
      // no status line on this surface
    }
    // One process inside the 1.5 s budget: save the dragged width, clear.
    await run($, ['shutdown', String(dragColumns)])
    return next(e)
  })

  // A close the person asked for turns the frame off, so the timer does not
  // open it again behind their back. The engine's unload (a reload) does not.
  on('ui.close', { id: PANE }, async ($, e, next) => {
    paneOpen = false
    if (dragColumns > 0 && dragColumns !== persistedDrag) {
      const saved = await run($, ['dock', String(dragColumns)])
      if (saved.ok) persistedDrag = dragColumns
    }
    if (e.origin.kind === 'person') {
      frameOn = false
      await run($, ['frame', 'off'])
      await run($, ['bg', 'off'])
    }
    return next(e)
  }).catch(($, e, next) => next.called ? next(e) : undefined)

  // A prompt the person entered may open a pane even below the unasked floor
  // of 144 columns (110 from here), which also recovers a /clear that closed it.
  on('prompt.submit', async ($, e, next) => {
    if (frameOn && !paneOpen && viewFullscreen && viewColumns >= 110) {
      await showPane($, openWidth(), true)
    }
    return next(e)
  }).catch(($, e, next) => next.called ? next(e) : undefined)

  on('command.run', { command: 'xiaoke' }, async ($, e) => {
    const raw = (e.args ?? '').trim()
    if (raw === '' || raw === 'list' || raw === 'current') {
      return { text: (await run($, [raw || 'list'])).out }
    }

    const [head, ...rest] = raw.split(/\s+/)

    if (head === 'frame') {
      const mode = rest[0] ?? 'toggle'
      const wanted = mode === 'on'
        ? true
        : mode === 'off'
          ? false
          : (await read($, 'frame')) !== 'on'
      if (!wanted) {
        frameOn = false
        await run($, ['frame', 'off'])
        await hidePane($)
        return { text: '画框已关闭' }
      }
      if (!e.presentation.isFullscreen) {
        return { text: '画框只在 Claude Code 的 fullscreen TUI 下停靠右侧；先运行 /tui fullscreen。' }
      }
      if (e.presentation.columns < 110) {
        return { text: `画框需要至少 110 列，当前 ${e.presentation.columns} 列。` }
      }
      frameOn = true
      everOpened = true
      await run($, ['frame', 'on'])
      const dockCols = dragColumns > 0
        ? dragColumns
        : Math.max(40, Math.floor(e.presentation.columns * 0.44))
      const shown = await showPane($, dockCols, true)
      return { text: shown.ok ? `画框已停靠右侧（${dockCols} 列）` : shown.out }
    }

    if (head === 'set') {
      const result = await run($, ['set', ...rest])
      await ensureFrame($, e)
      return { text: result.out }
    }

    if (head === 'theme' || head === 'tz' || head === 'auto' || head === 'bg'
      || head === 'colors' || head === 'install' || head === 'path' || head === 'get'
      || head === 'rows' || head === 'sync') {
      const out = (await run($, [head, ...rest])).out
      // auto/tz can move the scene to another look's image: keep the frame up.
      if (head === 'auto' || head === 'tz') {
        await ensureFrame($, e)
      }
      return { text: out }
    }

    // A bare name is a look: /xiaoke 樱花
    const result = await run($, ['set', raw])
    await ensureFrame($, e)
    return { text: result.out }
  })

  // Always drawn, so a terminal resize is seen even while the pane is shut.
  // Render hooks are raised on every attached surface: a phone beside the
  // terminal must not overwrite the terminal's width.
  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    const viewport = e.viewport
    if (e.surface === 'terminal' && viewport) {
      viewColumns = viewport.columns
      viewFullscreen = viewport.isFullscreen !== false
    }
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, ($, e) => {
    const { Box } = $.ui.resolve(e)
    const columns = e.props.bodyColumns
    if (e.surface === 'terminal' && columns > 0) {
      dockColumns = columns
      panePlacement = e.props.placement ?? 'dock'
      // A width that differs from what we asked for is the person's drag;
      // the default stays adaptive and is never written to disk.
      if (paneOpen && panePlacement === 'dock'
        && requestedColumns > 0 && Math.abs(columns - requestedColumns) > 1) {
        dragColumns = columns
      }
    }
    // Deliberately empty and unpainted: the pane reserves blank cells and
    // kitty's background image shows through them.
    return <Box width={columns} height={e.props.scroll.bodyRows} />
  })
}
