import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Solo en la versión compilada servida por HTTPS: Electron (app://) y Vite quedan fuera.
if (import.meta.env.PROD && window.location.protocol === 'https:' && 'serviceWorker' in navigator) {
  void navigator.serviceWorker.register('/sw.js');
}
