// Preload — exposes a small, typed surface to the renderer.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('tcAPI', {
  apiPort: () => ipcRenderer.invoke('api-port'),
  openFile: () => ipcRenderer.invoke('open-file'),
  openFolder: (p) => ipcRenderer.invoke('open-folder', p),
  showInFolder: (p) => ipcRenderer.invoke('show-in-folder', p),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  saveAs: (defaultName, srcPath) => ipcRenderer.invoke('save-as', { defaultName, srcPath }),
  startCarla: () => ipcRenderer.invoke('start-carla'),
});
