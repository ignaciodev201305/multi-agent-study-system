/// <reference types="vite/client" />

interface Window {
  // Solo existe dentro de Electron; en el navegador es undefined.
  desktop?: {
    platform: string;
    electronVersion: string;
    getLocalToken: () => Promise<string>;
  };
}
