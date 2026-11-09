import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('desktop', {
  platform: process.platform,
  electronVersion: process.versions.electron ?? '',
  getLocalToken: (): Promise<string> => ipcRenderer.invoke('local-token'),
});
