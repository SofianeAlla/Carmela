// Electron main process.
// - Spawns the Python FastAPI sidecar on a free port.
// - Opens the main window, points the renderer at the sidecar URL.
// - Ensures the sidecar dies when the window closes.

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('node:path');
const { spawn } = require('node:child_process');
const net = require('node:net');
const fs = require('node:fs');

let mainWindow = null;
let py = null;
let apiPort = 5174;
const isPackaged = app.isPackaged;

const PROJECT_ROOT = isPackaged
  ? path.join(process.resourcesPath)
  : path.join(__dirname, '..');

function pythonExe() {
  const venv = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venv)) return venv;
  return 'python';
}

async function pickFreePort(startAt = 5174) {
  for (let p = startAt; p < startAt + 50; p++) {
    const free = await new Promise((resolve) => {
      const srv = net.createServer();
      srv.once('error', () => resolve(false));
      srv.once('listening', () => srv.close(() => resolve(true)));
      srv.listen(p, '127.0.0.1');
    });
    if (free) return p;
  }
  throw new Error('no free port in 5174..5223');
}

function spawnSidecar(port) {
  const exe = pythonExe();
  const env = {
    ...process.env,
    API_PORT: String(port),
    API_HOST: '127.0.0.1',
    PYTHONPATH: PROJECT_ROOT,
    PYTHONUNBUFFERED: '1',
  };
  console.log('[trellis-carla] starting sidecar:', exe, '-m', 'api.main', 'on port', port);
  py = spawn(exe, ['-m', 'api.main', '--port', String(port)], {
    cwd: PROJECT_ROOT,
    env,
    windowsHide: true,
  });
  py.stdout.on('data', (b) => process.stdout.write(`[py] ${b}`));
  py.stderr.on('data', (b) => process.stderr.write(`[py] ${b}`));
  py.on('exit', (code) => {
    console.log('[trellis-carla] sidecar exited with', code);
    py = null;
  });
}

async function waitForApi(port, timeoutMs = 25000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) return true;
    } catch (_) {
      // sidecar not up yet
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function appIconPath() {
  // Prefer the user-supplied PNG/ICO if present; else fall back to the SVG.
  const candidates = [
    path.join(__dirname, 'renderer', 'assets', 'moose-logo.ico'),
    path.join(__dirname, 'renderer', 'assets', 'moose-logo.png'),
    path.join(__dirname, 'renderer', 'assets', 'moose-logo.svg'),
    path.join(__dirname, 'renderer', 'assets', 'moose.svg'),
  ];
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return undefined;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 960,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#11141c',
    title: 'Carmela by Bespoke AI | Free 3D AI Asset Generation for Carla Simulator',
    icon: appIconPath(),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

async function bootstrap() {
  apiPort = await pickFreePort(5174);
  spawnSidecar(apiPort);
  const up = await waitForApi(apiPort);
  if (!up) {
    dialog.showErrorBox(
      'Sidecar failed to start',
      'The Python FastAPI sidecar did not respond within 25s. ' +
        'Check that the .venv exists and dependencies are installed.'
    );
  }
  createWindow();
}

// ----- IPC handlers used by the renderer -----

ipcMain.handle('api-port', () => apiPort);

ipcMain.handle('open-file', async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
  });
  if (r.canceled || r.filePaths.length === 0) return null;
  return r.filePaths[0];
});

ipcMain.handle('open-folder', (_e, p) => {
  shell.openPath(p);
});

ipcMain.handle('open-external', (_e, url) => {
  if (typeof url !== 'string') return { ok: false, error: 'bad url' };
  if (!/^https?:\/\//.test(url)) return { ok: false, error: 'http(s) only' };
  shell.openExternal(url);
  return { ok: true };
});

ipcMain.handle('show-in-folder', (_e, p) => {
  if (!p || typeof p !== 'string') return { ok: false, error: 'no path' };
  const abs = resolveAssetPath(p);
  if (!abs) return { ok: false, error: `not found: ${p}` };
  shell.showItemInFolder(abs);
  return { ok: true };
});

function resolveAssetPath(src) {
  // Library entries store paths relative to PROJECT_ROOT (the trellis_carla
  // dir the FastAPI sidecar runs from). Resolve them against that root.
  if (!src) return null;
  if (path.isAbsolute(src) && fs.existsSync(src)) return src;
  const candidates = [
    path.join(PROJECT_ROOT, src),
    path.join(PROJECT_ROOT, 'trellis_carla', src),
    path.resolve(src),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

ipcMain.handle('save-as', async (_e, { defaultName, srcPath }) => {
  const abs = resolveAssetPath(srcPath);
  if (!abs) return { ok: false, error: `source not found: ${srcPath}` };
  const ext = path.extname(defaultName || abs).replace(/^\./, '') || 'glb';
  const r = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName || path.basename(abs),
    filters: [{ name: ext.toUpperCase(), extensions: [ext] }, { name: 'All', extensions: ['*'] }],
  });
  if (r.canceled || !r.filePath) return { ok: false, canceled: true };
  try {
    fs.copyFileSync(abs, r.filePath);
    return { ok: true, dest: r.filePath };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
});

ipcMain.handle('start-carla', () => {
  const carla = path.join(process.env.USERPROFILE || '', 'CARLA_0.9.16', 'CarlaUE4.exe');
  if (!fs.existsSync(carla)) {
    return { ok: false, error: `CarlaUE4.exe not found at ${carla}` };
  }
  spawn(carla, [], { detached: true, stdio: 'ignore' }).unref();
  return { ok: true };
});

app.whenReady().then(bootstrap);

app.on('window-all-closed', () => {
  if (py) {
    try {
      py.kill();
    } catch (_) {
      // best effort
    }
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (py) py.kill();
});
